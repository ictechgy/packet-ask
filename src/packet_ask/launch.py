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


def launch_kimi(packet: Packet, timeout: int) -> str:
    """v1에서 Kimi 자동 실행은 하지 않는다."""
    raise PacketAskError(
        "kimi -p 는 도구를 자동 승인합니다. --provider paste 를 사용하세요.",
        codes.CONFINEMENT,
    )
