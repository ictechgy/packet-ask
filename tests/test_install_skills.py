"""install-skills 가 하니스 홈에 SKILL.md 를 심는다."""

from pathlib import Path

import pytest

from packet_ask.errors import PacketAskError
from packet_ask.install_skills import SKILL_RELATIVE_PATHS, install_skills


def test_install_skills_writes_claude_codex_grok(tmp_path: Path) -> None:
    """Claude, Codex, Grok, agents 경로에 같은 스킬을 설치한다."""
    written = install_skills(home=tmp_path)
    names = {path.name for path in written}
    assert names == {"SKILL.md"}
    rel = {path.relative_to(tmp_path).as_posix() for path in written}
    assert rel == set(SKILL_RELATIVE_PATHS)
    text = (tmp_path / ".claude/skills/packet-ask/SKILL.md").read_text(encoding="utf-8")
    assert "packet-ask" in text
    assert "UNTRUSTED PROVIDER OUTPUT" in text
    assert "user-invocable: true" in text
    assert "MAIN" in text


def test_install_skills_refuses_to_overwrite_custom(tmp_path: Path) -> None:
    """다른 내용의 SKILL.md 는 --force 없이 덮지 않는다."""
    path = tmp_path / ".claude/skills/packet-ask/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("custom skill\n", encoding="utf-8")
    with pytest.raises(PacketAskError):
        install_skills(home=tmp_path)
    assert path.read_text(encoding="utf-8") == "custom skill\n"


def test_install_skills_force_overwrites(tmp_path: Path) -> None:
    """--force 이면 기존 스킬을 패키지 원문으로 바꾼다."""
    path = tmp_path / ".claude/skills/packet-ask/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("custom skill\n", encoding="utf-8")
    install_skills(home=tmp_path, force=True)
    assert "packet-ask" in path.read_text(encoding="utf-8")


def test_install_skills_rejects_intermediate_symlink(tmp_path: Path) -> None:
    """하니스 경로의 중간 심링크를 따라가 쓰지 않는다."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".claude").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PacketAskError):
        install_skills(home=tmp_path)
    assert not (outside / "skills" / "packet-ask" / "SKILL.md").exists()
