"""Claude / Codex / Grok 사용자 스킬 디렉터리에 SKILL.md 를 설치한다."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.text import message

SKILL_RELATIVE_PATHS = (
    ".claude/skills/packet-ask/SKILL.md",
    ".grok/skills/packet-ask/SKILL.md",
    ".codex/skills/packet-ask/SKILL.md",
    ".agents/skills/packet-ask/SKILL.md",
)


def skill_markdown() -> str:
    """패키지에 실은 스킬 원문을 읽는다."""
    return files("packet_ask.data").joinpath("SKILL.md").read_text(encoding="utf-8")


def install_skills(home: Path | None = None, force: bool = False) -> list[Path]:
    """각 하니스 홈에 packet-ask 스킬을 기록한다."""
    root = home if home is not None else Path.home()
    body = skill_markdown()
    written: list[Path] = []
    for relative in SKILL_RELATIVE_PATHS:
        path = root / relative
        _write_skill(path, body, force)
        written.append(path)
    return written


def _write_skill(path: Path, body: str, force: bool) -> None:
    """심링크가 아닌 경로에만 쓰고, 다른 내용은 force 없이 덮지 않는다."""
    if path.exists() or path.is_symlink():
        _replace_existing_skill(path, body, force)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise PacketAskError(message("skill_symlink"), codes.CONFINEMENT)
    path.write_text(body, encoding="utf-8")


def _replace_existing_skill(path: Path, body: str, force: bool) -> None:
    """기존 파일이 패키지 원문과 같으면 두고, 다르면 force 만 허용한다."""
    if path.is_symlink():
        raise PacketAskError(message("skill_symlink"), codes.CONFINEMENT)
    existing = path.read_text(encoding="utf-8")
    if existing == body:
        return
    if not force:
        raise PacketAskError(message("skill_exists"), codes.USAGE)
    path.write_text(body, encoding="utf-8")
