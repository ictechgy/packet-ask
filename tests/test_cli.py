"""CLI 종료 코드와 paste 출력."""

import subprocess
from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask.cli import main


def _init_repo(root: Path) -> Path:
    """테스트용 저장소를 만든다."""
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
    assert not leftover.exists() or not any(leftover.iterdir())


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
    monkeypatch.setattr("packet_ask.launch.shutil.which", lambda name: None if name == "kimi" else __import__("shutil").which(name))
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
    assert code == codes.PROVIDER_MISSING
