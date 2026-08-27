"""Claude / Codex / Grok 사용자 스킬 디렉터리에 SKILL.md 를 설치한다."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

SKILL_RELATIVE_PATHS = (
    ".claude/skills/packet-ask/SKILL.md",
    ".grok/skills/packet-ask/SKILL.md",
    ".codex/skills/packet-ask/SKILL.md",
    ".agents/skills/packet-ask/SKILL.md",
)


def skill_markdown() -> str:
    """패키지에 실은 스킬 원문을 읽는다."""
    return files("packet_ask.data").joinpath("SKILL.md").read_text(encoding="utf-8")


def install_skills(home: Path | None = None) -> list[Path]:
    """각 하니스 홈에 packet-ask 스킬을 기록한다."""
    root = home if home is not None else Path.home()
    body = skill_markdown()
    written: list[Path] = []
    for relative in SKILL_RELATIVE_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        written.append(path)
    return written
