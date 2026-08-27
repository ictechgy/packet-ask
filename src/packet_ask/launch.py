"""공식 CLI를 최소 환경에서 한 번만 실행한다."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from packet_ask import codes
from packet_ask.doctor import inspect_providers
from packet_ask.errors import PacketAskError
from packet_ask.packet import Packet

GLM_ENDPOINT = "https://api.z.ai/api/anthropic"


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
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/opt/homebrew/bin"),
        "LANG": os.environ.get("LANG", "C"),
        "LC_ALL": os.environ.get("LC_ALL", "C"),
        "TMPDIR": str(home / "tmp"),
    }
    env.update(extra)
    return env


def run_isolated_command(
    executable: Path,
    argv: list[str],
    stdin_text: str,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
) -> str:
    """stdin으로 패킷을 넣고 stdout만 돌려받는다."""
    try:
        result = subprocess.run(
            [str(executable), *argv],
            input=stdin_text,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PacketAskError("프로바이더가 시간 제한을 넘겼습니다.", codes.PROVIDER_FAILED) from exc
    if result.returncode != 0:
        raise PacketAskError("프로바이더가 실패했습니다.", codes.PROVIDER_FAILED)
    return result.stdout


def require_launchable(provider: str) -> None:
    """doctor가 실행을 허용하지 않으면 막는다."""
    statuses = {item.name: item for item in inspect_providers()}
    status = statuses.get(provider)
    if status is None or not status.can_launch:
        note = status.note if status else "알 수 없는 프로바이더"
        raise PacketAskError(note, codes.CONFINEMENT)


def launch_glm(packet: Packet, timeout: int) -> str:
    """공식 claude 바이너리를 GLM 엔드포인트로 한 번 호출한다."""
    require_launchable("glm")
    key = os.environ.get("PACKET_ASK_GLM_KEY", "").strip()
    if not key:
        raise PacketAskError(
            "PACKET_ASK_GLM_KEY 환경변수가 없습니다. 전역 ANTHROPIC 키를 쓰지 않습니다.",
            codes.PROVIDER_MISSING,
        )
    executable = shutil.which("claude")
    if not executable:
        raise PacketAskError("claude CLI가 없습니다.", codes.PROVIDER_MISSING)
    home = provider_home("glm")
    env = isolated_env(
        home,
        {
            "ANTHROPIC_BASE_URL": GLM_ENDPOINT,
            "ANTHROPIC_API_KEY": key,
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
            "CLAUDE_CONFIG_DIR": str(home / "claude-config"),
        },
    )
    stdin_text = (packet.root / "packet.md").read_text(encoding="utf-8")
    argv = [
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
    return run_isolated_command(Path(executable), argv, stdin_text, packet.root, env, timeout)


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


def _toml_string(value: str) -> str:
    """TOML 기본 문자열로 이스케이프한다."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _ensure_kimi_config(kimi_home: Path, api_key: str) -> None:
    """격리 홈에 최소 config와 빈 MCP를 둔다. 기존 로그인 파일은 키 없을 때 유지한다."""
    kimi_home.mkdir(parents=True, exist_ok=True)
    kimi_home.chmod(0o700)
    config = kimi_home / "config.toml"
    if api_key:
        body = (
            "telemetry = false\n"
            "default_yolo = false\n"
            "[tools]\n"
            'enabled = ["*"]\n'
            "[providers.kimi.env]\n"
            f"KIMI_API_KEY = {_toml_string(api_key)}\n"
        )
        config.write_text(body, encoding="utf-8")
        config.chmod(0o600)
    elif not config.is_file() or config.stat().st_size == 0:
        raise PacketAskError(
            "PACKET_ASK_KIMI_KEY 가 없고 격리 프로필에도 로그인이 없습니다.",
            codes.PROVIDER_MISSING,
        )
    mcp = kimi_home / "mcp.json"
    mcp.write_text('{"mcpServers":{}}\n', encoding="utf-8")
    mcp.chmod(0o600)


def _cleanup_kimi_sessions(kimi_home: Path) -> None:
    """실행 후 세션 로그를 지워 패킷 사본이 홈에 남지 않게 한다."""
    sessions = kimi_home / "sessions"
    if sessions.is_dir():
        shutil.rmtree(sessions, ignore_errors=True)


def launch_kimi(packet: Packet, timeout: int) -> str:
    """공식 kimi CLI를 TUI 없이 한 번 호출한다. 도구는 에이전트 파일로 끈다."""
    executable = shutil.which("kimi")
    if not executable:
        raise PacketAskError("kimi CLI가 없습니다.", codes.PROVIDER_MISSING)
    require_launchable("kimi")
    api_key = os.environ.get("PACKET_ASK_KIMI_KEY", "").strip()
    home = provider_home("kimi")
    kimi_home = home / "kimi-code"
    _ensure_kimi_config(kimi_home, api_key)
    agent_file = write_kimi_no_tools_agent(packet.root)
    skills_dir = packet.root / ".pa-skills"
    skills_dir.mkdir(exist_ok=True)
    skills_dir.chmod(0o700)
    env = isolated_env(
        home,
        {
            "KIMI_CODE_HOME": str(kimi_home),
            "KIMI_DISABLE_TELEMETRY": "1",
        },
    )
    stdin_text = (packet.root / "packet.md").read_text(encoding="utf-8")
    argv = kimi_launch_args(packet.root, agent_file, skills_dir)
    try:
        return run_isolated_command(Path(executable), argv, stdin_text, packet.root, env, timeout)
    finally:
        _cleanup_kimi_sessions(kimi_home)

