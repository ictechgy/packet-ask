"""CLI 종료 코드와 paste 출력."""

import subprocess
from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask.cli import main


def _init_repo(root: Path) -> Path:
    """테스트용 저장소를 만든다."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=root, check=True)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/app.py"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    return root


def test_review_paste_prints_untrusted_packet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """paste 리뷰는 패킷을 봉투에 넣어 출력한다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--question",
            "이 코드의 문제를 찾아줘",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert "UNTRUSTED PROVIDER OUTPUT" in captured.out
    assert "print(1)" in captured.out
    leftover = repo / ".packet-ask-tmp"
    assert not leftover.exists()


def test_rejects_implementation_without_vendor(monkeypatch: pytest.MonkeyPatch) -> None:
    """구현 요청은 벤더 없이 거절한다."""
    code = main(
        ["review", "--provider", "paste", "--question", "이 기능을 구현해줘"]
    )
    assert code == codes.POLICY


def test_research_requires_question() -> None:
    """research 는 질문이 없으면 usage 오류."""
    code = main(["research", "--provider", "paste"])
    assert code == codes.USAGE


def test_claude_without_dedicated_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """claude 서브는 전역 Anthropic 키를 쓰지 않는다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("PACKET_ASK_CLAUDE_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-used")
    code = main(
        [
            "review",
            "--provider",
            "claude",
            "--files",
            "src/app.py",
            "--question",
            "이 코드를 리뷰해줘",
        ]
    )
    assert code in {codes.PROVIDER_MISSING, codes.CONFINEMENT}


def test_glm_without_dedicated_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GLM은 전역 Anthropic 키가 아니라 PACKET_ASK_GLM_KEY 만 받는다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("PACKET_ASK_GLM_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-used")
    code = main(
        [
            "review",
            "--provider",
            "glm",
            "--files",
            "src/app.py",
            "--question",
            "이 코드를 리뷰해줘",
        ]
    )
    assert code in {codes.PROVIDER_MISSING, codes.CONFINEMENT}


def test_kimi_without_cli_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kimi 바이너리가 없으면 벤더를 실행하지 않는다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "packet_ask.launch.resolve_trusted_executable",
        lambda name: None if name == "kimi" else __import__("packet_ask.paths", fromlist=["resolve_trusted_executable"]).resolve_trusted_executable(name),
    )
    code = main(
        [
            "review",
            "--provider",
            "kimi",
            "--files",
            "src/app.py",
            "--question",
            "이 코드를 리뷰해줘",
        ]
    )
    assert code in {codes.PROVIDER_MISSING, codes.CONFINEMENT}


def test_grok_provider_is_paste_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """grok 는 실행하지 않고 패킷만 출력한다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "grok",
            "--files",
            "src/app.py",
            "--question",
            "이 코드의 문제를 찾아줘",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert "UNTRUSTED PROVIDER OUTPUT" in captured.out
    assert "print(1)" in captured.out


def test_providers_json_lists_builtins(capsys: pytest.CaptureFixture[str]) -> None:
    """providers --json 에 내장 id 가 있다."""
    import json

    code = main(["providers", "--json"])
    assert code == codes.SUCCESS
    rows = json.loads(capsys.readouterr().out)
    ids = {row["id"] for row in rows}
    assert {"paste", "glm", "kimi", "claude", "grok", "agy"} <= ids


def test_unknown_provider_is_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """카탈로그에 없는 id 는 usage."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "nope",
            "--files",
            "src/app.py",
            "--question",
            "이 코드를 리뷰해줘",
        ]
    )
    assert code == codes.USAGE


def test_review_without_explicit_scope_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """unstaged 가 있어도 --files/--diff/--staged/--unstaged 없으면 거절한다."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code = main(["review", "--provider", "paste", "--question", "이 변경을 리뷰해줘"])
    assert code == codes.SCOPE


def test_review_unstaged_sends_working_tree_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--unstaged 를 명시하면 워킹 트리 diff 만 보낸다."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--unstaged",
            "--question",
            "이 변경을 리뷰해줘",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert "print(2)" in captured.out


def test_review_include_files_is_not_a_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """review 의 --include-files 는 범위를 만들지 않고 거절한다."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--include-files",
            "src/app.py",
            "--question",
            "이 변경을 리뷰해줘",
        ]
    )
    assert code in {codes.USAGE, codes.SCOPE, codes.POLICY}


def test_research_rejects_local_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """research 는 --diff 로 로컬 코드를 첨부하지 않는다."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code = main(
        [
            "research",
            "--provider",
            "paste",
            "--diff",
            "HEAD",
            "--question",
            "이 변경의 공개 자료를 찾아줘",
        ]
    )
    assert code == codes.POLICY


def test_review_rejects_files_and_diff_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """review 는 스코프 플래그를 하나만 받는다."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--diff",
            "HEAD",
            "--question",
            "리뷰해줘",
        ]
    )
    assert code == codes.SCOPE


def test_review_budget_counts_question_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """질문과 파일을 합친 패킷 예산을 넘기면 거절한다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--max-bytes",
            "40",
            "--question",
            "x" * 80,
        ]
    )
    assert code == codes.BUDGET


def test_review_prints_receipt_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """벤더 전에 보내는 경로를 stderr 로 알려 준다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--question",
            "리뷰해줘",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert "src/app.py" in captured.err
    assert "paste" in captured.err


def test_review_prints_timing_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """성공 시 stderr 에 비밀 없는 구간 시간을 쓴다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_GLM_KEY", "glm-secret-must-not-leak")
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--question",
            "리뷰해줘",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    import re

    timing_lines = [line for line in captured.err.splitlines() if line.startswith("packet-ask timing")]
    assert len(timing_lines) == 1
    assert re.fullmatch(
        r"packet-ask timing preflight_ms=\d+ packet_ms=\d+ launch_ms=\d+ total_ms=\d+",
        timing_lines[0],
    )
    assert "glm-secret-must-not-leak" not in captured.err
    assert "glm-secret-must-not-leak" not in captured.out


def test_review_json_includes_timing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json 봉투에 밀리초 구간만 넣고 키 값은 넣지 않는다."""
    import json

    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_KIMI_KEY", "kimi-secret-must-not-leak")
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--json",
            "--question",
            "리뷰해줘",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    data = json.loads(captured.out)
    timing = data["timing"]
    assert set(timing) == {"preflight_ms", "packet_ms", "launch_ms", "total_ms"}
    for key in timing:
        assert isinstance(timing[key], int)
        assert timing[key] >= 0
    assert timing["total_ms"] >= timing["launch_ms"]
    dumped = json.dumps(data)
    assert "kimi-secret-must-not-leak" not in dumped
    assert "kimi-secret-must-not-leak" not in captured.err


def test_review_json_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json 은 versioned 봉투만 stdout 에 쓴다."""
    import json

    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--json",
            "--question",
            "리뷰해줘",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    data = json.loads(captured.out)
    assert data["schema"] == "packet-ask.v1"
    assert data["ok"] is True
    assert data["receipt"]["provider"] == "paste"
    assert "src/app.py" in data["receipt"]["paths"]
    assert "untrusted_output" in data
    assert "print(1)" in data["untrusted_output"]


def test_review_paste_uses_cache_dir_not_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """패킷 부모는 OS 캐시이며 레포 안에 만들지 않는다."""
    repo = _init_repo(tmp_path / "repo")
    cache = tmp_path / "cache"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_CACHE_DIR", str(cache))
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--question",
            "이 코드의 문제를 찾아줘",
        ]
    )
    assert code == codes.SUCCESS
    assert not (repo / ".packet-ask-tmp").exists()
    leftovers = list(cache.glob("packet-ask-*")) if cache.exists() else []
    assert leftovers == []
