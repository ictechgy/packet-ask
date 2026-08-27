"""공식 CLI를 최소 환경에서 한 번만 실행한다."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path

from packet_ask import codes
from packet_ask.doctor import inspect_providers
from packet_ask.errors import PacketAskError
from packet_ask.packet import Packet
from packet_ask.paths import minimal_child_env, resolve_trusted_executable

GLM_ENDPOINT = "https://api.z.ai/api/anthropic"
KIMI_DISABLED_TOOL_SENTINEL = "packet-ask-no-such-tool"


def provider_home(name: str) -> Path:
    """패킷과 분리된 인증 프로필 경로."""
    base = Path.home() / ".config" / "packet-ask" / "providers" / name
    base.mkdir(parents=True, exist_ok=True)
    base.chmod(0o700)
    tmp = base / "tmp"
    tmp.mkdir(exist_ok=True)
    tmp.chmod(0o700)
    return base


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
    try:
        stdout, _stderr = proc.communicate(input=stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_group(proc)
        try:
            proc.communicate(timeout=1)
        except (subprocess.TimeoutExpired, OSError):
            pass
        raise PacketAskError("프로바이더가 시간 제한을 넘겼습니다.", codes.PROVIDER_FAILED) from exc
    if proc.returncode != 0:
        raise PacketAskError("프로바이더가 실패했습니다.", codes.PROVIDER_FAILED)
    return stdout or ""


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


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    """세션 리더와 손자 프로세스까지 종료한다."""
    if proc.pid is None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        proc.kill()
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except OSError:
            break
        try:
            proc.wait(timeout=1)
            return
        except subprocess.TimeoutExpired:
            continue


def require_launchable(provider: str) -> None:
    """doctor가 실행을 허용하지 않으면 막는다."""
    statuses = {item.name: item for item in inspect_providers()}
    status = statuses.get(provider)
    if status is None or not status.can_launch:
        note = status.note if status else "알 수 없는 프로바이더"
        raise PacketAskError(note, codes.CONFINEMENT)


def _require_executable(name: str) -> Path:
    """신뢰 경로의 공식 CLI만 고른다. 셸 래퍼 PATH는 쓰지 않는다."""
    found = resolve_trusted_executable(name)
    if found is None:
        raise PacketAskError(f"{name} CLI가 신뢰 경로에 없습니다.", codes.PROVIDER_MISSING)
    return found


def _require_glm_key() -> str:
    """전역 Anthropic 키가 아니라 PACKET_ASK_GLM_KEY 만 받는다."""
    key = os.environ.get("PACKET_ASK_GLM_KEY", "").strip()
    if not key:
        raise PacketAskError(
            "PACKET_ASK_GLM_KEY 환경변수가 없습니다. 전역 ANTHROPIC 키를 쓰지 않습니다.",
            codes.PROVIDER_MISSING,
        )
    return key


def _glm_child_env(home: Path, key: str) -> dict[str, str]:
    """Z.ai 공식 Claude Code 연동만 자식 환경에 넣는다. 부모 셸은 바꾸지 않는다."""
    return {
        "ANTHROPIC_BASE_URL": GLM_ENDPOINT,
        "ANTHROPIC_API_KEY": key,
        "ANTHROPIC_AUTH_TOKEN": key,
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "CLAUDE_CONFIG_DIR": str(home / "claude-config"),
    }


def _glm_argv() -> list[str]:
    """무도구 plan 원샷. --bare 는 Anthropic OAuth 대신 자식 키만 쓰게 한다."""
    return [
        "--bare",
        "-p",
        "--tools",
        "",
        "--permission-mode",
        "plan",
        "--no-session-persistence",
        "--setting-sources",
        "",
    ]


def launch_glm(packet: Packet, timeout: int) -> str:
    """공식 claude 바이너리를 GLM 엔드포인트로 한 번 호출한다."""
    require_launchable("glm")
    key = _require_glm_key()
    executable = _require_executable("claude")
    home = provider_home("glm")
    stdin_text = (packet.root / "packet.md").read_text(encoding="utf-8")
    env = isolated_env(home, _glm_child_env(home, key))
    return run_isolated_command(executable, _glm_argv(), stdin_text, packet.root, env, timeout)


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
    kimi_home.mkdir(parents=True, exist_ok=True)
    kimi_home.chmod(0o700)
    body = (
        "telemetry = false\n"
        "default_yolo = false\n"
        "[tools]\n"
        f'enabled = ["{KIMI_DISABLED_TOOL_SENTINEL}"]\n'
    )
    config = kimi_home / "config.toml"
    config.write_text(body, encoding="utf-8")
    config.chmod(0o600)
    mcp = kimi_home / "mcp.json"
    mcp.write_text('{"mcpServers":{}}\n', encoding="utf-8")
    mcp.chmod(0o600)


def _cleanup_kimi_sessions(kimi_home: Path) -> None:
    """실행 후 세션 로그를 지워 패킷 사본이 홈에 남지 않게 한다."""
    sessions = kimi_home / "sessions"
    if sessions.is_dir():
        shutil.rmtree(sessions, ignore_errors=True)


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
    executable = _require_executable("kimi")
    require_launchable("kimi")
    api_key = os.environ.get("PACKET_ASK_KIMI_KEY", "").strip()
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
