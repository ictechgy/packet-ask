"""install-skills 가 하니스 홈에 SKILL.md 를 심는다."""

from pathlib import Path

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
    assert "지금 이 세션을 돌리는 에이전트" in text
