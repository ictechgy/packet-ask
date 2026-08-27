"""설치된 공식 CLI가 무도구 원샷을 지원하는지 검사한다."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from packet_ask.paths import minimal_child_env, resolve_trusted_executable

_HELP_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class ProviderStatus:
    """프로바이더별 실행 가능 여부. 비밀 값은 담지 않는다."""

    name: str
    installed: bool
    can_launch: bool
    note: str


def has_cli_flag(help_text: str, flag: str) -> bool:
    """help 텍스트에서 플래그를 단어 경계로 찾는다. -p 가 --path 에 오탐하지 않게 한다."""
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text) is not None


def claude_supports_isolated_print(help_text: str) -> bool:
    """Claude Code help에 격리 원샷에 필요한 플래그가 있는지 본다."""
    needed = ("--bare", "--tools", "--no-session-persistence", "--setting-sources")
    return all(has_cli_flag(help_text, flag) for flag in needed)


def kimi_supports_print(help_text: str) -> bool:
    """Kimi help에 print/prompt 원샷이 있는지 본다. 도구 차단은 별도."""
    return any(has_cli_flag(help_text, flag) for flag in ("-p", "--prompt", "--print"))


def kimi_supports_isolated_print(help_text: str) -> bool:
    """무도구 원샷에 필요한 agent-file 과 work-dir 이 있는지 본다."""
    has_prompt = kimi_supports_print(help_text) or has_cli_flag(help_text, "--quiet")
    has_agent = has_cli_flag(help_text, "--agent-file")
    has_workdir = has_cli_flag(help_text, "--work-dir") or has_cli_flag(help_text, "-w")
    return has_prompt and has_agent and has_workdir


def _help_text(executable: str) -> str | None:
    """--help 를 최소 환경에서 가져온다. 실패하면 None."""
    path = resolve_trusted_executable(executable)
    if path is None:
        return None
    probe = Path(tempfile.mkdtemp(prefix="packet-ask-probe-"))
    probe.chmod(0o700)
    try:
        result = subprocess.run(
            [str(path), "--help"],
            check=False,
            capture_output=True,
            text=True,
            cwd=probe,
            env=minimal_child_env(probe),
            timeout=_HELP_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
        return (result.stdout or "") + (result.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def inspect_providers() -> list[ProviderStatus]:
    """로컬 claude/kimi 상태를 수집한다. 모델 호출은 하지 않는다."""
    statuses: list[ProviderStatus] = []
    claude_help = _help_text("claude")
    if claude_help is None:
        statuses.append(ProviderStatus("glm", False, False, "claude CLI가 없습니다."))
    elif claude_supports_isolated_print(claude_help):
        statuses.append(
            ProviderStatus(
                "glm",
                True,
                True,
                "claude --bare/--tools/--no-session-persistence 가 보입니다. 키는 자식 환경에만 넣습니다.",
            )
        )
    else:
        statuses.append(ProviderStatus("glm", True, False, "필요한 claude 플래그가 없어 paste만 가능합니다."))
    kimi_help = _help_text("kimi")
    if kimi_help is None:
        statuses.append(ProviderStatus("kimi", False, False, "kimi CLI가 없습니다."))
    elif kimi_supports_isolated_print(kimi_help):
        statuses.append(
            ProviderStatus(
                "kimi",
                True,
                True,
                "kimi quiet/print + --agent-file(tools: []) + --work-dir 로 원샷합니다. 키는 PACKET_ASK_KIMI_KEY 이며 디스크에 쓰지 않습니다.",
            )
        )
    elif kimi_supports_print(kimi_help):
        statuses.append(
            ProviderStatus("kimi", True, False, "--agent-file/--work-dir 가 없어 격리 원샷을 못 합니다.")
        )
    else:
        statuses.append(ProviderStatus("kimi", True, False, "원샷 플래그를 확인하지 못했습니다."))
    statuses.append(ProviderStatus("paste", True, True, "벤더 프로세스 없이 패킷만 출력합니다."))
    return statuses
