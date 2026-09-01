"""사람이 커밋한 공개 표면 선언."""

import json
import subprocess
from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask.cli import main
from packet_ask.errors import ScopeError
from packet_ask.surface import SURFACE_FILENAME, load_surface


def _init_repo(root: Path) -> Path:
    """src/app.py 와 secrets/notes.txt 를 가진 저장소를 만든다."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=root, check=True)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    (root / "private").mkdir()
    (root / "private" / "notes.txt").write_text("internal\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    return root


def _declare(repo: Path, body: str) -> None:
    """공개 표면을 선언한다."""
    (repo / SURFACE_FILENAME).write_text(body, encoding="utf-8")


def test_absent_surface_leaves_behaviour_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """선언 파일이 없으면 강제는 꺼져 있다. 기존 사용자를 깨지 않는다."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    code = main(
        ["review", "--provider", "paste", "--files", "private/notes.txt",
         "--question", "이 변경을 리뷰해줘"]
    )
    assert code == codes.SUCCESS
    assert "surface=absent" in capsys.readouterr().err


def test_declared_paths_are_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """선언 안의 경로는 그대로 나간다."""
    repo = _init_repo(tmp_path / "repo")
    _declare(repo, "# 공개해도 되는 범위\nsrc\n")
    monkeypatch.chdir(repo)
    code = main(
        ["review", "--provider", "paste", "--files", "src/app.py",
         "--question", "이 변경을 리뷰해줘"]
    )
    assert code == codes.SUCCESS
    assert "surface=enforced" in capsys.readouterr().err


def test_undeclared_path_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """선언 밖 경로는 벤더 시작 전에 거절한다."""
    repo = _init_repo(tmp_path / "repo")
    _declare(repo, "src\n")
    monkeypatch.chdir(repo)
    code = main(
        ["review", "--provider", "paste", "--files", "private/notes.txt",
         "--question", "이 변경을 리뷰해줘"]
    )
    captured = capsys.readouterr()
    assert code == codes.SCOPE
    assert captured.out == ""
    assert "internal" not in captured.out


def test_prefix_match_is_by_path_component_not_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`src` 선언이 `srcret/` 를 열어주면 안 된다."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "srcret").mkdir()
    (repo / "srcret" / "leak.txt").write_text("nope\n", encoding="utf-8")
    _declare(repo, "src\n")
    monkeypatch.chdir(repo)
    assert main(
        ["review", "--provider", "paste", "--files", "srcret/leak.txt",
         "--question", "이 변경을 리뷰해줘"]
    ) == codes.SCOPE


def test_override_is_recorded_not_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """우회는 가능하지만 영수증에 남는다. 조용한 우회가 아니다."""
    repo = _init_repo(tmp_path / "repo")
    _declare(repo, "src\n")
    monkeypatch.chdir(repo)
    code = main(
        ["review", "--provider", "paste", "--files", "private/notes.txt",
         "--outside-surface", "--json", "--question", "이 변경을 리뷰해줘"]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert "surface=overridden" in captured.err
    assert json.loads(captured.out)["receipt"]["surface"] == "overridden"


def test_diff_paths_are_surface_checked_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """선언 밖 diff 도 거절한다.

    `--diff <임의 ref>` 는 워크트리를 하나도 건드리지 않고 과거 내용을 꺼낼 수
    있으므로 "diff 는 사람 작업의 발자국"이라는 면제 논거가 성립하지 않는다.
    """
    repo = _init_repo(tmp_path / "repo")
    _declare(repo, "src\n")
    (repo / "private" / "notes.txt").write_text("changed\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code = main(
        ["review", "--provider", "paste", "--unstaged", "--question", "이 변경을 리뷰해줘"]
    )
    captured = capsys.readouterr()
    assert code == codes.SCOPE
    assert captured.out == ""
    assert "changed" not in captured.out


def test_declared_diff_still_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """선언 안의 변경을 리뷰하는 평범한 흐름은 그대로 동작한다."""
    repo = _init_repo(tmp_path / "repo")
    _declare(repo, "src\n")
    (repo / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    assert main(
        ["review", "--provider", "paste", "--unstaged", "--question", "이 변경을 리뷰해줘"]
    ) == codes.SUCCESS


def test_history_diff_cannot_bypass_the_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """워크트리 흔적 0 으로 과거 내용을 꺼내는 경로를 막는다."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "private" / "notes.txt").write_text("second\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=d@e", "-c", "user.name=D", "commit", "-m", "second"],
        cwd=repo, check=True, capture_output=True,
    )
    _declare(repo, "src\n")
    monkeypatch.chdir(repo)
    assert main(
        ["review", "--provider", "paste", "--diff", "HEAD~1", "--question", "이 변경을 리뷰해줘"]
    ) == codes.SCOPE


@pytest.mark.parametrize(
    "body",
    ["/etc/passwd\n", "../outside\n", "src/*\n", "src\x00bad\n", "\n\n", "#만 있음\n"],
)
def test_malformed_declarations_fail_closed(tmp_path: Path, body: str) -> None:
    """절대 경로·상위 참조·글롭·제어문자·빈 선언은 거절한다."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _declare(repo, body)
    with pytest.raises(ScopeError):
        load_surface(repo)


def test_surface_file_must_not_be_a_symlink(tmp_path: Path) -> None:
    """선언 파일이 심링크면 대상이 바뀌어 조용히 범위가 넓어진다."""
    repo = tmp_path / "repo"
    repo.mkdir()
    real = tmp_path / "elsewhere"
    real.write_text("src\n", encoding="utf-8")
    (repo / SURFACE_FILENAME).symlink_to(real)
    with pytest.raises(ScopeError):
        load_surface(repo)


def test_inspect_reports_surface_without_a_vendor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """inspect 도 같은 검사를 거치고 상태를 공개한다."""
    repo = _init_repo(tmp_path / "repo")
    _declare(repo, "src\n")
    monkeypatch.chdir(repo)
    code = main(["inspect", "review", "--files", "src/app.py", "--json", "--question", "t"])
    assert code == codes.SUCCESS
    assert json.loads(capsys.readouterr().out)["summary"]["surface"] == "enforced"


def test_research_include_files_is_surface_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """README 는 두 파일 플래그를 모두 거절한다고 적었다. research 경로도 고정한다."""
    repo = _init_repo(tmp_path / "repo")
    _declare(repo, "src\n")
    monkeypatch.chdir(repo)
    assert main(
        ["research", "--provider", "paste", "--include-files", "private/notes.txt",
         "--question", "이 주제를 조사해줘"]
    ) == codes.SCOPE


def test_ledger_records_the_surface_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """우회 사실이 대장에도 남아야 사후 감사가 가능하다."""
    repo = _init_repo(tmp_path / "repo")
    _declare(repo, "src\n")
    target = tmp_path / "egress.jsonl"
    monkeypatch.setenv("PACKET_ASK_LEDGER", str(target))
    monkeypatch.chdir(repo)
    assert main(
        ["review", "--provider", "paste", "--files", "private/notes.txt",
         "--outside-surface", "--question", "이 변경을 리뷰해줘"]
    ) == codes.SUCCESS
    assert json.loads(target.read_text(encoding="utf-8").strip())["surface"] == "overridden"


def test_a_bom_does_not_silently_kill_the_first_declaration(tmp_path: Path) -> None:
    """BOM 은 strip 대상이 아니라 첫 선언을 영원히 매칭 안 되게 만든다."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _declare(repo, "\ufeffsrc\n")
    assert load_surface(repo) == ("src",)


def test_candidate_dot_segments_never_match(tmp_path: Path) -> None:
    """호출자가 정규화하더라도 이 모듈이 자기 불변식을 지킨다."""
    from packet_ask.surface import assert_within_surface

    with pytest.raises(ScopeError):
        assert_within_surface(["src/../private/notes.txt"], ("src",))
