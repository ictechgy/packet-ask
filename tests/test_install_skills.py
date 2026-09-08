"""install-skills 가 하니스 홈에 SKILL.md 를 심는다."""

from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask.cli import main
from packet_ask.install_skills import SKILL_RELATIVE_PATHS, install_skills
from packet_ask.text import message


def test_install_skills_writes_claude_codex_grok(tmp_path: Path) -> None:
    """Claude, Codex, Grok, agents 경로에 같은 스킬을 설치한다."""
    report = install_skills(home=tmp_path)
    written = report.written
    names = {path.name for path in written}
    assert names == {"SKILL.md"}
    rel = {path.relative_to(tmp_path).as_posix() for path in written}
    assert rel == set(SKILL_RELATIVE_PATHS)
    assert report.failures == ()
    text = (tmp_path / ".claude/skills/packet-ask/SKILL.md").read_text(encoding="utf-8")
    assert "packet-ask" in text
    assert "UNTRUSTED PROVIDER OUTPUT" in text
    assert "user-invocable: true" in text
    assert "MAIN" in text
    # MAIN 이 질문을 argv로 흘리지 않고 0.4.0 packet shape 플래그를 알도록 고정한다.
    assert "--question-stdin" in text
    assert "--line-numbers" in text
    assert "--selected-tree" in text
    # 선택형 보호 실행은 명시적 요청 때만 외부 실행기로 분기한다. 자동 설치·
    # 기본값 변경·조용한 우회가 없음을 스킬에서 고정한다.
    assert "packet-ask-safe" in text
    assert "silently fall back" in text
    # 보호 실행기는 별도 macOS 설치물이며 패키지에 실리지 않는다. 없으면
    # 미지원으로 보고한다. "설치된 런처"만 적으면 없는 문을 가리키게 된다.
    assert "not shipped with this package" in text
    assert "unsupported" in text
    assert "report that and stop" in text
    assert "--use-keychain" in text
    assert "--credential-source" in text


def test_install_skills_refuses_to_overwrite_custom(tmp_path: Path) -> None:
    """다른 내용의 SKILL.md 는 --force 없이 덮지 않는다."""
    path = tmp_path / ".claude/skills/packet-ask/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("custom skill\n", encoding="utf-8")
    report = install_skills(home=tmp_path)
    assert path.read_text(encoding="utf-8") == "custom skill\n"
    assert path not in report.written
    assert [item.relative for item in report.failures] == [
        ".claude/skills/packet-ask/SKILL.md"
    ]
    assert report.failures[0].code == codes.USAGE


def test_install_skills_force_overwrites(tmp_path: Path) -> None:
    """--force 이면 기존 스킬을 패키지 원문으로 바꾼다."""
    path = tmp_path / ".claude/skills/packet-ask/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("custom skill\n", encoding="utf-8")
    report = install_skills(home=tmp_path, force=True)
    assert "packet-ask" in path.read_text(encoding="utf-8")
    assert report.failures == ()


def test_install_skills_rejects_intermediate_symlink(tmp_path: Path) -> None:
    """하니스 경로의 중간 심링크를 따라가 쓰지 않는다."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".claude").symlink_to(outside, target_is_directory=True)
    report = install_skills(home=tmp_path)
    assert not (outside / "skills" / "packet-ask" / "SKILL.md").exists()
    assert report.failures[0].code == codes.CONFINEMENT


def test_install_skills_proceeds_past_a_broken_home(tmp_path: Path) -> None:
    """한 홈이 깨져도 나머지는 설치되고 실패는 보고용으로 모은다.

    기존 동작은 첫 실패에서 예외를 던졌다. 그 시점까지 쓴 홈은 이미 쓰여
    있는데 반환 목록이 통째로 사라져 CLI 는 한 줄도 출력하지 않았다. 실측:
    두 번째 홈이 심링크면 첫 홈은 쓰인 채 exit 13 이고 보고는 0줄이었다.
    나머지 홈은 낡은 SKILL.md 를 들고 남는데 무엇이 설치됐는지 알 수 없었다.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".grok").symlink_to(outside, target_is_directory=True)

    report = install_skills(home=tmp_path)

    written = {path.relative_to(tmp_path).as_posix() for path in report.written}
    assert written == {
        ".claude/skills/packet-ask/SKILL.md",
        ".codex/skills/packet-ask/SKILL.md",
        ".agents/skills/packet-ask/SKILL.md",
    }
    assert [item.relative for item in report.failures] == [
        ".grok/skills/packet-ask/SKILL.md"
    ]
    assert report.failures[0].code == codes.CONFINEMENT
    # 가드는 그대로다. 심링크를 따라 쓰지 않는다.
    assert not (outside / "skills" / "packet-ask" / "SKILL.md").exists()


def test_install_skills_aggregates_mixed_failures(tmp_path: Path) -> None:
    """종류가 다른 실패도 모은다. 첫 실패에서 멈추지 않는다."""
    custom = tmp_path / ".claude/skills/packet-ask/SKILL.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("custom skill\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".grok").symlink_to(outside, target_is_directory=True)

    report = install_skills(home=tmp_path)

    assert {item.code for item in report.failures} == {codes.USAGE, codes.CONFINEMENT}
    assert len(report.failures) == 2
    assert len(report.written) == 2
    assert custom.read_text(encoding="utf-8") == "custom skill\n"


def test_install_skills_cli_reports_each_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI 가 쓴 경로와 실패한 홈을 각각 보고하고 종료 코드를 고른다.

    보고 목록만 단언하면 실패가 섞여도 녹색이므로, 심링크 홈(confinement)과
    다른 내용 홈(usage)을 함께 두고 코드 우선순위까지 본다. 격리 검사가 사용
    오류보다 무거우므로 13 이다. stdout 은 쓴 경로만, stderr 는 실패만 싣는다.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    custom = home / ".claude/skills/packet-ask/SKILL.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("custom skill\n", encoding="utf-8")
    outside = home / "outside"
    outside.mkdir()
    (home / ".grok").symlink_to(outside, target_is_directory=True)

    assert main(["install-skills"]) == codes.CONFINEMENT
    captured = capsys.readouterr()

    stdout_lines = [line for line in captured.out.splitlines() if line]
    assert len(stdout_lines) == 2
    assert all(line.endswith("SKILL.md") for line in stdout_lines)

    assert captured.err.count(message("skill_symlink")) == 1
    assert captured.err.count(message("skill_exists")) == 1
    assert ".grok/skills/packet-ask/SKILL.md" in captured.err
    assert ".claude/skills/packet-ask/SKILL.md" in captured.err
    # 성공 경로는 stderr 가 비어 있어야 한다. 조용한 성공과 구별하기 위해서다.
    assert main(["install-skills", "--force"]) == codes.CONFINEMENT
    forced = capsys.readouterr()
    assert forced.err.count(message("skill_symlink")) == 1
    assert message("skill_exists") not in forced.err
    assert len([line for line in forced.out.splitlines() if line]) == 3
