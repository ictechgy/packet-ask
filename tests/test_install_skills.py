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
    assert [(item.relative, item.code) for item in report.failures] == [
        (".claude/skills/packet-ask/SKILL.md", codes.CONFINEMENT)
    ]
    assert len(report.written) == len(SKILL_RELATIVE_PATHS) - 1


def test_non_utf8_existing_skill_is_reported_not_raised(tmp_path: Path) -> None:
    """non-UTF-8 로 저장한 기존 SKILL.md 는 예외가 아니라 홈 실패로 모인다.

    실측한 결함이다. `_replace_existing_skill` 의 `read_text` 가
    UnicodeDecodeError 를 던지고 그것이 `install_skills` 밖으로 새어 나가
    트레이스백이 됐으며, 그 시점까지 쓴 홈만 남고 나머지 홈은 시도되지
    않았다. 이 변경이 고치려던 "쓰인 채 보고 없음" 상태가 이 경로에서 그대로
    재현됐다. 읽지 못하면 원문과 같다는 것을 증명할 수 없으므로 덮지 않고,
    `--force` 로도 풀리지 않는다 — 그래서 usage 가 아니라 confinement 다.
    """
    broken = tmp_path / ".claude/skills/packet-ask/SKILL.md"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"custom \xff\xfe skill\n")

    report = install_skills(home=tmp_path)

    assert [(item.relative, item.code, item.reason_key) for item in report.failures] == [
        (
            ".claude/skills/packet-ask/SKILL.md",
            codes.CONFINEMENT,
            "skill_read_failed",
        )
    ]
    assert broken.read_bytes() == b"custom \xff\xfe skill\n"
    assert len(report.written) == len(SKILL_RELATIVE_PATHS) - 1


def test_unwritable_home_directory_is_reported_not_raised(tmp_path: Path) -> None:
    """쓰지 못한 홈도 예외 대신 실패로 모은다.

    mkdir/write 의 OSError 는 `PacketAskError` 가 아니므로 이전에는 그대로
    새어 나갔다. root-owned `.claude` 처럼 현실적으로 생긴다. 실측: 두 번째
    홈을 0500 으로 두면 PermissionError 가 나고 첫 홈만 쓰인 채 멈췄다.
    root 로 돌리면 이 테스트는 통과해 버리므로 CI 사용자 권한을 전제한다.
    """
    locked = tmp_path / ".grok"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        report = install_skills(home=tmp_path)
    finally:
        locked.chmod(0o700)

    assert [(item.relative, item.code, item.reason_key) for item in report.failures] == [
        (".grok/skills/packet-ask/SKILL.md", codes.CONFINEMENT, "skill_write_failed")
    ]
    assert len(report.written) == len(SKILL_RELATIVE_PATHS) - 1


def test_failure_reasons_come_from_the_message_catalog(tmp_path: Path) -> None:
    """사유는 렌더된 문장이 아니라 카탈로그 키로 담긴다.

    문장을 보고서에 담으면 실패 시점의 언어 설정이 굳고, 미래의 메시지가 경로를
    보간하기 시작해도 보고서 쪽에서 막을 수 없다. 허용된 키 집합을 고정해서
    새 실패 종류가 사유 없이 지나가지 않게 한다.
    """
    from packet_ask.install_skills import REASON_KEYS

    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".grok").symlink_to(outside, target_is_directory=True)
    custom = tmp_path / ".claude/skills/packet-ask/SKILL.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("custom skill\n", encoding="utf-8")

    report = install_skills(home=tmp_path)
    assert {item.reason_key for item in report.failures} <= REASON_KEYS
    assert REASON_KEYS == frozenset(
        {"skill_symlink", "skill_exists", "skill_read_failed", "skill_write_failed"}
    )
    for item in report.failures:
        assert message(item.reason_key)


def test_install_exit_code_prefers_confinement_over_usage() -> None:
    """세 분기를 직접 본다. CLI 테스트만 보면 두 분기는 무테스트로 남는다."""
    from packet_ask.install_skills import SkillInstallFailure, install_exit_code

    usage = SkillInstallFailure(".claude/skills/packet-ask/SKILL.md", codes.USAGE, "skill_exists")
    confinement = SkillInstallFailure(
        ".grok/skills/packet-ask/SKILL.md", codes.CONFINEMENT, "skill_symlink"
    )
    assert install_exit_code(()) == codes.SUCCESS
    assert install_exit_code((usage,)) == codes.USAGE
    assert install_exit_code((confinement,)) == codes.CONFINEMENT
    # --force 로 풀리는 실패와 풀리지 않는 실패가 섞이면 풀리지 않는 쪽을 말한다.
    assert install_exit_code((usage, confinement)) == codes.CONFINEMENT
    assert install_exit_code((confinement, usage)) == codes.CONFINEMENT


def test_install_skills_proceeds_past_a_broken_home(tmp_path: Path) -> None:
    """한 홈이 깨져도 나머지는 설치되고 실패는 보고용으로 모은다.

    기존 동작은 첫 실패에서 예외를 던졌다. 그 시점까지 쓴 홈은 이미 쓰여
    있는데 반환 목록이 통째로 사라져 CLI 는 한 줄도 출력하지 않았다. 실측:
    두 번째 홈이 심링크면 첫 홈은 쓰인 채 exit 13, 보고 0줄, 나머지 두 홈은
    낡은 SKILL.md. 문제는 부분 설치가 아니라 보고 없는 부분 설치였다.
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
    assert [(item.relative, item.code) for item in report.failures] == [
        (".grok/skills/packet-ask/SKILL.md", codes.CONFINEMENT)
    ]
    # 가드는 그대로다. 심링크를 따라 쓰지 않는다.
    assert not (outside / "skills" / "packet-ask" / "SKILL.md").exists()


def test_install_skills_aggregates_mixed_failures(tmp_path: Path) -> None:
    """종류가 다른 실패도 모은다. 어느 홈이 어느 코드인지까지 고정한다.

    코드 집합과 개수만 보면 홈과 코드의 짝이 바뀌어도 녹색이다.
    """
    custom = tmp_path / ".claude/skills/packet-ask/SKILL.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("custom skill\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".grok").symlink_to(outside, target_is_directory=True)

    report = install_skills(home=tmp_path)

    assert [(item.relative, item.code) for item in report.failures] == [
        (".claude/skills/packet-ask/SKILL.md", codes.USAGE),
        (".grok/skills/packet-ask/SKILL.md", codes.CONFINEMENT),
    ]
    assert {path.relative_to(tmp_path).as_posix() for path in report.written} == {
        ".codex/skills/packet-ask/SKILL.md",
        ".agents/skills/packet-ask/SKILL.md",
    }
    assert custom.read_text(encoding="utf-8") == "custom skill\n"


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """가짜 홈을 만들고 Path.home 을 거기로 돌린다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_install_skills_cli_reports_a_clean_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """깨끗한 홈에서는 exit 0, stderr 는 비고 stdout 은 네 경로다.

    조용한 성공과 보고된 성공을 구별하는 양성 대조다. 이것이 없으면 "실패가
    보고된다"는 단언은 실패가 애초에 없는 설정에서도 참이 된다.
    """
    home = _home(tmp_path, monkeypatch)
    expected = {str(home / relative) for relative in SKILL_RELATIVE_PATHS}

    assert main(["install-skills"]) == codes.SUCCESS
    captured = capsys.readouterr()
    assert captured.err == ""
    assert {line for line in captured.out.splitlines() if line} == expected


def test_install_skills_cli_usage_failure_exits_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """confinement 가 섞이지 않으면 첫 실패 코드인 usage(2)로 끝난다.

    문서화한 분기인데 심링크 홈이 섞인 케이스만 있어 무테스트였다.
    """
    home = _home(tmp_path, monkeypatch)
    custom = home / ".claude/skills/packet-ask/SKILL.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("custom skill\n", encoding="utf-8")

    assert main(["install-skills"]) == codes.USAGE
    captured = capsys.readouterr()
    assert captured.err.splitlines() == [
        message(
            "skill_home_failed",
            name=".claude/skills/packet-ask/SKILL.md",
            reason=message("skill_exists"),
        )
    ]
    assert custom.read_text(encoding="utf-8") == "custom skill\n"


def test_install_skills_cli_reports_each_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI 가 설치된 경로와 실패한 홈을 각각 보고하고 종료 코드를 고른다.

    부분문자열 단언은 `name` 과 `reason` 을 바꿔 끼워도 통과한다. 전체 줄이
    카탈로그에서 조립한 문장과 같은지 보고, stdout 은 절대경로 집합으로 본다.
    """
    home = _home(tmp_path, monkeypatch)
    custom = home / ".claude/skills/packet-ask/SKILL.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("custom skill\n", encoding="utf-8")
    outside = home / "outside"
    outside.mkdir()
    (home / ".grok").symlink_to(outside, target_is_directory=True)

    assert main(["install-skills"]) == codes.CONFINEMENT
    captured = capsys.readouterr()

    assert {line for line in captured.out.splitlines() if line} == {
        str(home / ".codex/skills/packet-ask/SKILL.md"),
        str(home / ".agents/skills/packet-ask/SKILL.md"),
    }
    assert sorted(captured.err.splitlines()) == sorted(
        [
            message(
                "skill_home_failed",
                name=".claude/skills/packet-ask/SKILL.md",
                reason=message("skill_exists"),
            ),
            message(
                "skill_home_failed",
                name=".grok/skills/packet-ask/SKILL.md",
                reason=message("skill_symlink"),
            ),
        ]
    )

    # --force 는 usage 실패만 해결한다. confinement 는 그대로 남는다.
    assert main(["install-skills", "--force"]) == codes.CONFINEMENT
    forced = capsys.readouterr()
    assert forced.err.splitlines() == [
        message(
            "skill_home_failed",
            name=".grok/skills/packet-ask/SKILL.md",
            reason=message("skill_symlink"),
        )
    ]
    # 이미 같은 내용은 다시 쓰지 않지만 그 홈도 설치된 상태로 보고된다.
    assert {line for line in forced.out.splitlines() if line} == {
        str(home / ".claude/skills/packet-ask/SKILL.md"),
        str(home / ".codex/skills/packet-ask/SKILL.md"),
        str(home / ".agents/skills/packet-ask/SKILL.md"),
    }
