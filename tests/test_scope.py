"""워크트리 스코프 수집과 거절 규칙."""

from pathlib import Path

import pytest

from packet_ask.errors import BudgetError, ScopeError
from packet_ask.scope import collect_files, collect_git_diff, is_secret_path, resolve_worktree


def _init_repo(root: Path) -> Path:
    """테스트용 git 저장소를 만든다."""
    import subprocess

    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=root, check=True)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/app.py"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    return root


def test_resolve_worktree(tmp_path: Path) -> None:
    """git 루트를 반환한다."""
    repo = _init_repo(tmp_path)
    assert resolve_worktree(repo / "src") == repo.resolve()


def test_rejects_secret_filename(tmp_path: Path) -> None:
    """.env 은 수집하지 않는다."""
    repo = _init_repo(tmp_path)
    with pytest.raises(ScopeError):
        collect_files(repo, [repo / ".env"])


def test_rejects_path_outside_worktree(tmp_path: Path) -> None:
    """워크트리 밖 경로는 거절한다."""
    repo = _init_repo(tmp_path)
    outsider = tmp_path.parent / "outside.txt"
    outsider.write_text("nope\n", encoding="utf-8")
    with pytest.raises(ScopeError):
        collect_files(repo, [outsider])


def test_collects_regular_file(tmp_path: Path) -> None:
    """일반 소스 파일은 상대경로로 수집한다."""
    repo = _init_repo(tmp_path)
    files = collect_files(repo, [repo / "src" / "app.py"])
    assert files[0].relative == "src/app.py"
    assert "print(1)" in files[0].content


def test_budget_rejects_too_many_bytes(tmp_path: Path) -> None:
    """용량 예산을 넘기면 BudgetError."""
    repo = _init_repo(tmp_path)
    big = repo / "src" / "big.py"
    big.write_text("x" * 300_000, encoding="utf-8")
    with pytest.raises(BudgetError):
        collect_files(repo, [big], max_bytes=256_000)


def test_collect_git_diff(tmp_path: Path) -> None:
    """unstaged diff를 모은다."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    diff = collect_git_diff(repo, unstaged=True)
    assert "print(2)" in diff
    assert "diff --git" in diff


def test_tokenizer_filename_is_not_secret() -> None:
    """tokenizer.py 처럼 token 부분문자열만 있는 이름은 시크릿이 아니다."""
    assert is_secret_path(Path("src/tokenizer.py")) is False
    assert is_secret_path(Path("src/access_token.json")) is True
    assert is_secret_path(Path("src/id_rsa")) is True


def test_rejects_git_metadata_file(tmp_path: Path) -> None:
    """.git 아래 파일은 수집하지 않는다."""
    repo = _init_repo(tmp_path)
    git_config = repo / ".git" / "config"
    assert git_config.is_file()
    with pytest.raises(ScopeError, match="git"):
        collect_files(repo, [git_config])


def test_collect_git_diff_rejects_secret_path(tmp_path: Path) -> None:
    """diff 에 시크릿 파일명이 있으면 전체를 거절한다."""
    repo = _init_repo(tmp_path)
    key_file = repo / "src" / "id_rsa"
    key_file.write_text("placeholder\n", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "add", "src/id_rsa"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "key"], cwd=repo, check=True, capture_output=True)
    key_file.write_text("placeholder-2\n", encoding="utf-8")
    with pytest.raises(ScopeError, match="시크릿"):
        collect_git_diff(repo, unstaged=True)


def test_collect_git_diff_budget(tmp_path: Path) -> None:
    """diff 용량이 예산을 넘기면 BudgetError."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("x" * 10_000, encoding="utf-8")
    with pytest.raises(BudgetError):
        collect_git_diff(repo, unstaged=True, max_bytes=100)
