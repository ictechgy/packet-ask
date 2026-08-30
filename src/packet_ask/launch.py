"""공식 CLI를 최소 환경에서 한 번만 실행한다."""

from __future__ import annotations

import fcntl
import os
import select
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path

from packet_ask import codes
from packet_ask.doctor import inspect_provider
from packet_ask.errors import PacketAskError
from packet_ask.output import MAX_OUTPUT_BYTES
from packet_ask.packet import Packet
from packet_ask.paths import minimal_child_env, resolve_trusted_executable
from packet_ask.text import message

GLM_ENDPOINT = "https://api.z.ai/api/anthropic"
KIMI_DISABLED_TOOL_SENTINEL = "packet-ask-no-such-tool"


def provider_home(name: str) -> Path:
    """패킷과 분리된 인증 프로필 경로."""
    base = Path.home() / ".config"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PacketAskError(message("provider_path_invalid"), codes.CONFINEMENT) from exc
    for part in ("packet-ask", "providers", name):
        base = base / part
        _ensure_private_directory(base)
    tmp = base / "tmp"
    _ensure_private_directory(tmp)
    return base


def _ensure_private_directory(path: Path) -> None:
    """도구 소유 디렉터리를 만들고 최종 심링크·소유권을 검사한다."""
    if path.is_symlink():
        raise PacketAskError(message("provider_path_symlink"), codes.CONFINEMENT)
    try:
        path.mkdir(exist_ok=True)
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PacketAskError(message("provider_path_invalid"), codes.CONFINEMENT) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise PacketAskError(message("provider_path_invalid"), codes.CONFINEMENT)
        os.fchmod(descriptor, 0o700)
    except OSError as exc:
        raise PacketAskError(message("provider_path_invalid"), codes.CONFINEMENT) from exc
    finally:
        os.close(descriptor)


def _write_private_text(path: Path, content: str) -> None:
    """최종 심링크를 따르지 않고 0600 텍스트 파일을 쓴다."""
    if path.is_symlink():
        raise PacketAskError(message("provider_path_symlink"), codes.CONFINEMENT)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        path.chmod(0o600)
    except OSError as exc:
        raise PacketAskError(message("provider_path_symlink"), codes.CONFINEMENT) from exc


def isolated_env(home: Path, extra: dict[str, str]) -> dict[str, str]:
    """부모의 클라우드 키를 복사하지 않는 최소 환경."""
    return minimal_child_env(home, extra)


def run_isolated_command(
    executable: Path,
    argv: list[str],
    stdin_text: str,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
) -> str:
    """stdin으로 패킷을 넣고 stdout만 돌려받는다. timeout 시 프로세스 그룹을 죽인다."""
    proc = _spawn_isolated(executable, argv, cwd, env)
    pgid = proc.pid
    try:
        stdout, stderr = _communicate_bounded(proc, stdin_text, timeout, pgid)
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired as exc:
        _kill_process_group(proc, pgid)
        raise PacketAskError(message("provider_timeout"), codes.PROVIDER_FAILED) from exc
    except PacketAskError:
        _kill_process_group(proc, pgid)
        raise
    except (OSError, ValueError) as exc:
        _kill_process_group(proc, pgid)
        raise PacketAskError(message("provider_failed"), codes.PROVIDER_FAILED) from exc
    if proc.returncode != 0:
        raise PacketAskError(message("provider_failed"), codes.PROVIDER_FAILED)
    if _utf8_size(stdout) > MAX_OUTPUT_BYTES or _utf8_size(stderr) > MAX_OUTPUT_BYTES:
        raise PacketAskError(message("output_guard_size"), codes.OUTPUT_GUARD)
    return stdout


def _spawn_isolated(
    executable: Path,
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
) -> subprocess.Popen[str]:
    """새 세션으로 자식을 띄워 타임아웃 때 그룹 단위로 죽일 수 있게 한다."""
    return subprocess.Popen(
        [str(executable), *argv],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        text=True,
        start_new_session=True,
    )


def _utf8_size(text: str | bytes) -> int:
    """UTF-8 바이트 수."""
    if isinstance(text, bytes):
        return len(text)
    return len(text.encode("utf-8"))


def _set_nonblocking(stream: object) -> None:
    """파이프를 논블로킹으로 둔다."""
    fileno = getattr(stream, "fileno", None)
    if fileno is None:
        return
    fd = fileno()
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def _read_pipe(stream: object) -> bytes | None:
    """None 은 아직 데이터 없음, 빈 문자열은 EOF."""
    fileno = getattr(stream, "fileno", None)
    if fileno is None:
        return b""
    try:
        raw = os.read(fileno(), 8192)
    except BlockingIOError:
        return None
    except OSError:
        return b""
    return raw


def _communicate_bounded(
    proc: subprocess.Popen[str],
    stdin_text: str,
    timeout: int,
    pgid: int | None,
) -> tuple[str, str]:
    """stdout/stderr 를 한도 안에서 읽고, 넘치면 그룹을 죽인다."""
    deadline = time.monotonic() + max(timeout, 1)
    stdin_bytes = stdin_text.encode("utf-8")
    stdin_offset = 0
    stdin_stream = proc.stdin
    stdin_closed = stdin_stream is None
    if stdin_stream is not None:
        _set_nonblocking(stdin_stream)
    if proc.stdout is not None:
        _set_nonblocking(proc.stdout)
    if proc.stderr is not None:
        _set_nonblocking(proc.stderr)
    stdout_parts: list[bytes] = []
    stderr_parts: list[bytes] = []
    stdout_n = 0
    stderr_n = 0
    stdout_stream = proc.stdout
    stderr_stream = proc.stderr
    waiters = [item for item in (stdout_stream, stderr_stream) if item is not None]
    eof = {stream: False for stream in waiters}
    while not stdin_closed or not all(eof.values()):
        if stdout_n > MAX_OUTPUT_BYTES or stderr_n > MAX_OUTPUT_BYTES:
            _kill_process_group(proc, pgid)
            raise PacketAskError(message("output_guard_size"), codes.OUTPUT_GUARD)
        if time.monotonic() > deadline:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        readers = [stream for stream, done in eof.items() if not done]
        writers = [] if stdin_closed or stdin_stream is None else [stdin_stream]
        ready_read, ready_write, _ = select.select(readers, writers, [], 0.1)
        for stream in ready_write:
            try:
                written = os.write(stream.fileno(), stdin_bytes[stdin_offset : stdin_offset + 8192])
                stdin_offset += written
            except BlockingIOError:
                continue
            except (BrokenPipeError, OSError):
                stdin_offset = len(stdin_bytes)
            if stdin_offset >= len(stdin_bytes):
                try:
                    stream.close()
                except OSError:
                    pass
                stdin_closed = True
        for stream in ready_read:
            chunk = _read_pipe(stream)
            if chunk is None:
                continue
            if chunk == b"":
                eof[stream] = True
                continue
            if stream is stdout_stream:
                stdout_parts.append(chunk)
                stdout_n += _utf8_size(chunk)
            else:
                stderr_parts.append(chunk)
                stderr_n += _utf8_size(chunk)
    stdout = b"".join(stdout_parts).decode("utf-8", errors="replace")
    stderr = b"".join(stderr_parts).decode("utf-8", errors="replace")
    return stdout, stderr


def _kill_process_group(proc: subprocess.Popen[str], pgid: int | None) -> None:
    """spawn 때 저장한 그룹에 SIGTERM 후 항상 SIGKILL 을 보낸다."""
    if pgid is None:
        _kill_leader(proc)
        return
    _signal_group(pgid, signal.SIGTERM)
    _wait_briefly(proc)
    _signal_group(pgid, signal.SIGKILL)
    _wait_briefly(proc)


def _kill_leader(proc: subprocess.Popen[str]) -> None:
    """그룹 id 를 못 얻으면 세션 리더만 죽인다."""
    try:
        proc.kill()
    except OSError:
        return
    _wait_briefly(proc)


def _signal_group(pgid: int, sig: int) -> None:
    """프로세스 그룹에 시그널을 보낸다. 이미 없으면 무시한다."""
    try:
        os.killpg(pgid, sig)
    except OSError:
        return


def _wait_briefly(proc: subprocess.Popen[str]) -> None:
    """리더가 끝날 때까지 짧게 기다린다."""
    try:
        proc.wait(timeout=1)
    except (subprocess.TimeoutExpired, OSError):
        return


def require_launchable(provider: str) -> None:
    """고른 프로바이더만 프로브한다. 카탈로그 전체를 --help 하지 않는다."""
    status = inspect_provider(provider)
    if not status.can_launch:
        raise PacketAskError(status.note, codes.CONFINEMENT)


def _require_executable(name: str) -> Path:
    """신뢰 경로의 공식 CLI만 고른다. 셸 래퍼 PATH는 쓰지 않는다."""
    found = resolve_trusted_executable(name)
    if found is None:
        raise PacketAskError(message("missing_cli", name=name), codes.PROVIDER_MISSING)
    return found


def _require_dedicated_key(env_name: str, hint: str) -> str:
    """전용 키만 받는다. 부모 셸의 일반 키는 쓰지 않는다."""
    key = os.environ.get(env_name, "").strip()
    if not key:
        raise PacketAskError(hint, codes.PROVIDER_MISSING)
    return key


def _require_glm_key() -> str:
    """전역 Anthropic 키가 아니라 PACKET_ASK_GLM_KEY 만 받는다."""
    return _require_dedicated_key(
        "PACKET_ASK_GLM_KEY",
        message("missing_key", name="PACKET_ASK_GLM_KEY"),
    )


def _require_claude_key() -> str:
    """Anthropic 서브는 PACKET_ASK_CLAUDE_KEY 만 받는다."""
    return _require_dedicated_key(
        "PACKET_ASK_CLAUDE_KEY",
        message("missing_key", name="PACKET_ASK_CLAUDE_KEY"),
    )


def _require_kimi_key() -> str:
    """Kimi 서브는 PACKET_ASK_KIMI_KEY 만 받는다."""
    return _require_dedicated_key(
        "PACKET_ASK_KIMI_KEY",
        message("missing_key", name="PACKET_ASK_KIMI_KEY"),
    )


def _claude_isolation_env(home: Path) -> dict[str, str]:
    """Claude Code 자식에서 오토메모리·부가 트래픽·에러 리포팅을 끈다."""
    return {
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "CLAUDE_CONFIG_DIR": str(home / "claude-config"),
    }


def _glm_child_env(home: Path, key: str) -> dict[str, str]:
    """Z.ai 공식 Claude Code 연동만 자식 환경에 넣는다. 부모 셸은 바꾸지 않는다."""
    extra = _claude_isolation_env(home)
    extra["ANTHROPIC_BASE_URL"] = GLM_ENDPOINT
    extra["ANTHROPIC_API_KEY"] = key
    extra["ANTHROPIC_AUTH_TOKEN"] = key
    return extra


def glm_argv() -> list[str]:
    """무도구 plan 원샷. -p 의 다음 인자는 빈 프롬프트라 --tools 를 삼키지 않는다."""
    return [
        "--bare",
        "-p",
        "",
        "--tools",
        "",
        "--permission-mode",
        "plan",
        "--no-session-persistence",
        "--setting-sources",
        "",
    ]


def _glm_argv() -> list[str]:
    """glm_argv 별칭. 기존 호출부를 깨지 않는다."""
    return glm_argv()


def launch_glm(packet: Packet, timeout: int) -> str:
    """공식 claude 바이너리를 GLM 엔드포인트로 한 번 호출한다."""
    require_launchable("glm")
    key = _require_glm_key()
    executable = _require_executable("claude")
    home = provider_home("glm")
    stdin_text = (packet.root / "packet.md").read_text(encoding="utf-8")
    env = isolated_env(home, _glm_child_env(home, key))
    return run_isolated_command(executable, glm_argv(), stdin_text, packet.root, env, timeout)


def _claude_child_env(home: Path, key: str) -> dict[str, str]:
    """Anthropic 키만 자식에 넣는다. BASE_URL 은 설정하지 않는다."""
    extra = _claude_isolation_env(home)
    extra["ANTHROPIC_API_KEY"] = key
    extra["ANTHROPIC_AUTH_TOKEN"] = key
    return extra


def launch_claude(packet: Packet, timeout: int) -> str:
    """공식 claude 를 Anthropic 엔드포인트로 한 번 호출한다."""
    require_launchable("claude")
    key = _require_claude_key()
    executable = _require_executable("claude")
    home = provider_home("claude")
    stdin_text = (packet.root / "packet.md").read_text(encoding="utf-8")
    env = isolated_env(home, _claude_child_env(home, key))
    return run_isolated_command(executable, glm_argv(), stdin_text, packet.root, env, timeout)


_KIMI_NO_TOOLS_AGENT = """---
name: packet-ask-reader
description: Packet-only reader with all tools disabled
tools: []
subagents: []
---
You have no tools. Answer using only the user prompt. Do not try to read
the filesystem, run a shell, or call MCP. Do not implement code.
"""


def write_kimi_no_tools_agent(directory: Path) -> Path:
    """tools: [] 에이전트 파일을 패킷 안에 만든다."""
    path = directory / "packet-ask-reader.md"
    path.write_text(_KIMI_NO_TOOLS_AGENT, encoding="utf-8")
    path.chmod(0o600)
    return path


def kimi_launch_args(work_dir: Path, agent_file: Path, skills_dir: Path) -> list[str]:
    """TUI를 열지 않는 격리 원샷 인자. --yolo/--add-dir 은 넣지 않는다."""
    return [
        "--quiet",
        "--work-dir",
        str(work_dir),
        "--agent-file",
        str(agent_file),
        "--skills-dir",
        str(skills_dir),
    ]


def ensure_kimi_config(kimi_home: Path) -> None:
    """키 없는 최소 config. 도구 allowlist는 매칭되지 않는 이름만 둔다."""
    _ensure_private_directory(kimi_home)
    body = (
        "telemetry = false\n"
        "default_yolo = false\n"
        "[tools]\n"
        f'enabled = ["{KIMI_DISABLED_TOOL_SENTINEL}"]\n'
    )
    config = kimi_home / "config.toml"
    _write_private_text(config, body)
    mcp = kimi_home / "mcp.json"
    _write_private_text(mcp, '{"mcpServers":{}}\n')


def _cleanup_kimi_sessions(kimi_home: Path) -> None:
    """실행 후 세션 로그를 지워 패킷 사본이 홈에 남지 않게 한다."""
    sessions = kimi_home / "sessions"
    if not sessions.exists() and not sessions.is_symlink():
        return
    if sessions.is_symlink():
        raise PacketAskError(message("kimi_cleanup_failed"), codes.INTERNAL)
    try:
        shutil.rmtree(sessions, ignore_errors=False)
    except OSError as exc:
        raise PacketAskError(message("kimi_cleanup_failed"), codes.INTERNAL) from exc


def _kimi_child_env(home: Path, kimi_home: Path, api_key: str) -> dict[str, str]:
    """Kimi 키는 디스크가 아니라 자식 환경에만 넣는다."""
    extra = {
        "KIMI_CODE_HOME": str(kimi_home),
        "KIMI_DISABLE_TELEMETRY": "1",
    }
    if api_key:
        extra["KIMI_API_KEY"] = api_key
    return extra


def launch_kimi(packet: Packet, timeout: int) -> str:
    """공식 kimi CLI를 TUI 없이 한 번 호출한다. 도구는 에이전트 파일로 끈다."""
    require_launchable("kimi")
    api_key = _require_kimi_key()
    executable = _require_executable("kimi")
    home = provider_home("kimi")
    kimi_home = home / "kimi-code"
    ensure_kimi_config(kimi_home)
    agent_file = write_kimi_no_tools_agent(packet.root)
    skills_dir = packet.root / ".pa-skills"
    skills_dir.mkdir(exist_ok=True)
    skills_dir.chmod(0o700)
    env = isolated_env(home, _kimi_child_env(home, kimi_home, api_key))
    stdin_text = (packet.root / "packet.md").read_text(encoding="utf-8")
    argv = kimi_launch_args(packet.root, agent_file, skills_dir)
    try:
        return run_isolated_command(executable, argv, stdin_text, packet.root, env, timeout)
    finally:
        _cleanup_kimi_sessions(kimi_home)
