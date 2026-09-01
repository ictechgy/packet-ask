"""설치된 공식 CLI가 무도구 원샷을 지원하는지 검사한다."""

from __future__ import annotations

import os
import re
import select
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from packet_ask.paths import (
    minimal_child_env,
    resolve_trusted_executable,
    trusted_executable_candidate_exists,
)
from packet_ask.providers import ProviderSpec, load_catalog, resolve_provider_adapter
from packet_ask.signals import deferred_task_signals
from packet_ask.text import message

_HELP_TIMEOUT_SECONDS = 10
_HELP_OUTPUT_BYTES = 256 * 1024
_HELP_CACHE: dict[tuple[str, int, int, int, int], str | None] = {}

# doctor 성공은 "설치가 됐다"로 읽히고 그 다음 문장은 대개 "그러니 안전하다"이다.
# 그런데 `receipt.GUARANTEES` 는 성공한 task 에만 붙으므로, 사람이 이 도구를
# 믿을지 정하는 첫 화면인 doctor 에는 그 상쇄가 도착하지 않는다. 상쇄해야 할
# 신호보다 상쇄가 늦게 오면 안 된다.
# GUARANTEES 와 같은 이유로 산출값이 아니라 코드 상수다. 검사 로직이 바뀌어도
# 상수가 스스로 강해지지 않아야 한다.
DOCTOR_SIGNALS: Mapping[str, str] = MappingProxyType(
    {
        "verification": "flags-mentioned",
        "sandbox": "unproven",
        "signatures": "unverified",
    }
)
# 사람 줄과 상수가 갈라지지 않도록 같은 매핑에서 만든다. receipt 한 줄과 달리
# 부분집합이 아니라 전부이므로 별도 리터럴을 두면 드리프트만 생긴다.
_SIGNALS_LINE_BODY = ",".join(f"{key}:{value}" for key, value in DOCTOR_SIGNALS.items())


def format_doctor_signals_line() -> str:
    """doctor 가 스스로 밝히는 검증 수준 한 줄.

    영수증과 같은 규약으로 append-only 토큰만 쓴다. 뒤에 키가 붙을 수 있으니
    줄 끝에 정규식을 앵커하면 안 된다.
    """
    return f"packet-ask doctor signals={_SIGNALS_LINE_BODY}"


@dataclass(frozen=True)
class ProviderStatus:
    """프로바이더별 실행 가능 여부. 비밀 값은 담지 않는다."""

    name: str
    installed: bool
    can_launch: bool
    note: str
    source: str = "builtin"
    mode: str = "launch"


def has_cli_flag(help_text: str, flag: str) -> bool:
    """help 텍스트에서 플래그를 단어 경계로 찾는다. -p 가 --path 에 오탐하지 않게 한다."""
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text) is not None


def claude_supports_isolated_print(help_text: str) -> bool:
    """Claude Code help에 격리 원샷에 필요한 플래그가 있는지 본다."""
    needed = (
        "--bare",
        "-p",
        "--tools",
        "--permission-mode",
        "--no-session-persistence",
        "--setting-sources",
        "--mcp-config",
        "--strict-mcp-config",
    )
    return all(has_cli_flag(help_text, flag) for flag in needed)


def kimi_supports_print(help_text: str) -> bool:
    """Kimi help에 print/prompt 원샷이 있는지 본다. 도구 차단은 별도."""
    return any(has_cli_flag(help_text, flag) for flag in ("-p", "--prompt", "--print"))


def kimi_supports_isolated_print(help_text: str) -> bool:
    """launch 가 실제로 넣는 quiet/agent-file/work-dir/skills-dir 이 있는지 본다."""
    needed = ("--quiet", "--agent-file", "--work-dir", "--skills-dir")
    return all(has_cli_flag(help_text, flag) for flag in needed)


def _help_stat_key(path: Path) -> tuple[str, int, int, int, int] | None:
    """같은 inode 바이너리 재프로브를 피하기 위한 identity key."""
    try:
        info = path.stat()
    except OSError:
        return None
    return (
        str(path),
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_mtime_ns),
        int(info.st_size),
    )


def _help_text(executable: str) -> str | None:
    """--help 를 최소 환경에서 가져온다. 같은 파일은 프로세스 안에서 재사용한다."""
    path = resolve_trusted_executable(executable)
    if path is None:
        return None
    key = _help_stat_key(path)
    if key is not None and key in _HELP_CACHE:
        return _HELP_CACHE[key]
    text = _run_help(path)
    if key is not None and text is not None:
        for stale in [item for item in _HELP_CACHE if item[0] == key[0]]:
            _HELP_CACHE.pop(stale, None)
        _HELP_CACHE[key] = text
    return text


def _run_help(path: Path) -> str | None:
    """신뢰 경로 바이너리의 --help 를 bounded process group으로 실행한다."""
    probe = Path(tempfile.mkdtemp(prefix="packet-ask-probe-"))
    probe.chmod(0o700)
    process: subprocess.Popen[bytes] | None = None
    try:
        with deferred_task_signals():
            process = subprocess.Popen(
                [str(path), "--help"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=probe,
                env=minimal_child_env(probe),
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        return _read_help_output(process)
    except (_HelpProbeFailed, OSError, subprocess.TimeoutExpired):
        if process is not None:
            _stop_help_process_group(process)
        return None
    except BaseException:
        if process is not None:
            _stop_help_process_group(process)
        raise
    finally:
        if process is not None:
            _close_help_pipes(process)
        shutil.rmtree(probe, ignore_errors=True)


class _HelpProbeFailed(Exception):
    """help 출력이 허용된 자원 경계를 넘었다."""


def _read_help_output(process: subprocess.Popen[bytes]) -> str:
    """stdout/stderr 를 하나의 deadline과 합산 byte cap 아래에서 읽는다."""
    streams = [stream for stream in (process.stdout, process.stderr) if stream is not None]
    if not streams:
        raise _HelpProbeFailed
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
    parts: dict[object, list[bytes]] = {stream: [] for stream in streams}
    eof = {stream: False for stream in streams}
    total = 0
    deadline = time.monotonic() + _HELP_TIMEOUT_SECONDS
    while not all(eof.values()):
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            raise subprocess.TimeoutExpired(process.args, _HELP_TIMEOUT_SECONDS)
        readers = [stream for stream, done in eof.items() if not done]
        ready, _, _ = select.select(readers, [], [], min(0.1, remaining_time))
        for stream in ready:
            try:
                chunk = os.read(stream.fileno(), min(65536, _HELP_OUTPUT_BYTES - total + 1))
            except BlockingIOError:
                continue
            if not chunk:
                eof[stream] = True
                continue
            parts[stream].append(chunk)
            total += len(chunk)
            if total > _HELP_OUTPUT_BYTES:
                raise _HelpProbeFailed
    process.wait(timeout=1)
    stdout = b"".join(parts.get(process.stdout, [])).decode("utf-8", errors="replace")
    stderr = b"".join(parts.get(process.stderr, [])).decode("utf-8", errors="replace")
    return stdout + stderr


def _stop_help_process_group(process: subprocess.Popen[bytes]) -> None:
    """help leader와 pipe를 물려받은 descendant를 같은 그룹에서 끝낸다."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except OSError:
            pass
        try:
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _close_help_pipes(process: subprocess.Popen[bytes]) -> None:
    """성공·실패 경로 모두에서 부모 pipe descriptor를 닫는다."""
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


def inspect_provider(provider_id: str) -> ProviderStatus:
    """카탈로그에서 한 프로바이더만 검사한다. 런치 핫패스용."""
    for spec in load_catalog():
        if spec.provider_id == provider_id:
            return _status_for(spec)
    return ProviderStatus(provider_id, False, False, message("provider_unknown_status"), "unknown", "paste")


def inspect_providers() -> list[ProviderStatus]:
    """카탈로그의 로컬 상태를 수집한다. 모델 호출은 하지 않는다."""
    return [_status_for(spec) for spec in load_catalog()]


def _status_for(spec: ProviderSpec) -> ProviderStatus:
    """스펙 한 줄의 설치·실행 가능 여부를 본다."""
    adapter = resolve_provider_adapter(spec)
    if adapter is None or adapter.launcher_name is None:
        installed = spec.binary is None or resolve_trusted_executable(spec.binary) is not None
        return ProviderStatus(spec.provider_id, installed, True, spec.note, spec.source, spec.mode)
    return _launch_status(spec, adapter.doctor_kind)


def _launch_status(spec: ProviderSpec, doctor_kind: str | None) -> ProviderStatus:
    """실행형 내장의 help 플래그를 검사한다."""
    binary = spec.binary or spec.provider_id
    help_text = _help_text(binary)
    if help_text is None:
        candidate_exists = trusted_executable_candidate_exists(binary)
        return ProviderStatus(
            spec.provider_id,
            candidate_exists,
            False,
            message(
                "provider_cli_untrusted" if candidate_exists else "provider_cli_missing",
                name=binary,
            ),
            spec.source,
            spec.mode,
        )
    checks = {
        "claude": claude_supports_isolated_print,
        "kimi": kimi_supports_isolated_print,
    }
    checker = checks.get(doctor_kind or "")
    if checker is not None and checker(help_text):
        return ProviderStatus(spec.provider_id, True, True, spec.note, spec.source, spec.mode)
    if doctor_kind == "kimi" and kimi_supports_print(help_text):
        return ProviderStatus(
            spec.provider_id,
            True,
            False,
            message("kimi_flags_missing"),
            spec.source,
            spec.mode,
        )
    return ProviderStatus(
        spec.provider_id,
        True,
        False,
        message("launch_flags_missing"),
        spec.source,
        spec.mode,
    )
