"""워크트리 스코프 수집과 거절 규칙."""

import subprocess
from pathlib import Path

import pytest

from packet_ask.errors import BudgetError, ScopeError
from packet_ask.scope import (
    collect_files,
    collect_git_diff,
    is_secret_path,
    resolve_worktree,
)


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


def test_resolve_worktree_uses_bounded_metadata_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """worktree discovery도 bounded process 정책을 우회하지 않는다."""
    captured: dict[str, object] = {}

    def bounded(worktree: Path, extra: list[str], max_bytes: int) -> str:
        captured["worktree"] = worktree
        captured["extra"] = extra
        captured["max_bytes"] = max_bytes
        raise ScopeError("timeout")

    monkeypatch.setattr("packet_ask.scope.run_bounded_git", bounded)
    with pytest.raises(ScopeError):
        resolve_worktree(tmp_path)
    assert captured["extra"] == ["rev-parse", "--show-toplevel"]
    assert captured["max_bytes"] == 4096


def test_resolve_worktree_caps_metadata_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rev-parse의 비정상적으로 큰 stdout을 경로로 받아들이지 않는다."""
    monkeypatch.setattr("packet_ask.scope.run_bounded_git", lambda *_args: "x" * 5000)
    with pytest.raises(ScopeError):
        resolve_worktree(tmp_path)


def test_bounded_git_runner_has_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """모든 bounded git 호출은 공통 deadline을 적용한다."""
    script = tmp_path / "git"
    script.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    script.chmod(0o700)
    monkeypatch.setenv("PACKET_ASK_GIT_BIN", str(script))
    monkeypatch.setattr("packet_ask.scope.GIT_TIMEOUT_SECONDS", 0)
    with pytest.raises(ScopeError):
        resolve_worktree(tmp_path)


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
    assert is_secret_path(Path("config/secrets.yml")) is True
    assert is_secret_path(Path("client_secrets.json")) is True


def test_collect_files_rejects_binary_content(tmp_path: Path) -> None:
    """명시 파일의 NUL 포함 바이너리를 텍스트로 변환하지 않는다."""
    root = tmp_path.resolve()
    binary = root / "image.bin"
    binary.write_bytes(b"header\x00payload")
    with pytest.raises(ScopeError):
        collect_files(root, [binary])


def test_collect_git_diff_applies_file_count_budget(tmp_path: Path) -> None:
    """diff도 변경 경로 수에 max-files를 적용한다."""
    import subprocess

    repo = _init_repo(tmp_path)
    (repo / "src" / "other.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/other.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "other"], cwd=repo, check=True, capture_output=True)
    (repo / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    (repo / "src" / "other.py").write_text("print(3)\n", encoding="utf-8")
    with pytest.raises(BudgetError):
        collect_git_diff(repo, unstaged=True, max_files=1)


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
    with pytest.raises(ScopeError, match="Secret"):
        collect_git_diff(repo, unstaged=True)


def test_collect_git_diff_rejects_secret_rename(tmp_path: Path) -> None:
    """시크릿 파일을 안전한 이름으로 바꿔도 diff 전체를 거절한다."""
    import subprocess

    repo = _init_repo(tmp_path)
    key_file = repo / "src" / "id_rsa"
    key_file.write_text("placeholder\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/id_rsa"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "key"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "mv", "src/id_rsa", "src/harmless.txt"], cwd=repo, check=True, capture_output=True)
    with pytest.raises(ScopeError, match="Secret"):
        collect_git_diff(repo, staged=True)


def test_git_diff_disables_textconv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """git diff 는 textconv 과 외부 diff 를 끈다."""
    import subprocess

    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    seen: list[list[str]] = []
    seen_envs: list[dict[str, str]] = []
    real = subprocess.Popen

    def wrapper(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        cmd = list(args[0]) if args else list(kwargs.get("args", []))  # type: ignore[arg-type]
        seen.append([str(part) for part in cmd])
        env = kwargs.get("env")
        if isinstance(env, dict):
            seen_envs.append(env)
        return real(*args, **kwargs)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-secret")
    monkeypatch.setattr("packet_ask.scope.subprocess.Popen", wrapper)
    collect_git_diff(repo, unstaged=True)
    joined = [" ".join(item) for item in seen]
    assert any("--no-textconv" in item for item in joined)
    assert any("--name-status" in item and "-z" in item for item in joined)
    assert seen_envs
    assert all("ANTHROPIC_API_KEY" not in env for env in seen_envs)
    assert all("parent-secret" not in env.values() for env in seen_envs)


def test_collect_git_diff_budget(tmp_path: Path) -> None:
    """diff 용량이 예산을 넘기면 BudgetError."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("x" * 10_000, encoding="utf-8")
    with pytest.raises(BudgetError):
        collect_git_diff(repo, unstaged=True, max_bytes=100)


def test_git_interrupt_kills_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl+C가 bounded git 자식 그룹을 고아로 남기지 않는다."""
    script = tmp_path / "git"
    script.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    script.chmod(0o700)
    monkeypatch.setenv("PACKET_ASK_GIT_BIN", str(script))
    holder: dict[str, subprocess.Popen[bytes]] = {}
    real_popen = subprocess.Popen

    def spy(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        proc = real_popen(*args, **kwargs)
        holder["proc"] = proc
        return proc

    def interrupt(*_args: object, **_kwargs: object) -> tuple[list[object], list[object], list[object]]:
        raise KeyboardInterrupt

    monkeypatch.setattr("packet_ask.scope.subprocess.Popen", spy)
    monkeypatch.setattr("packet_ask.scope.select.select", interrupt)
    with pytest.raises(KeyboardInterrupt):
        collect_git_diff(tmp_path, unstaged=True)
    assert holder["proc"].poll() is not None


def test_git_setup_interrupt_kills_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stdout nonblocking 설정 중 Ctrl+C도 생성된 git 그룹을 종료한다."""
    script = tmp_path / "git"
    script.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    script.chmod(0o700)
    monkeypatch.setenv("PACKET_ASK_GIT_BIN", str(script))
    holder: dict[str, subprocess.Popen[bytes]] = {}
    real_popen = subprocess.Popen

    def spy(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        proc = real_popen(*args, **kwargs)
        holder["proc"] = proc
        return proc

    def interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("packet_ask.scope.subprocess.Popen", spy)
    monkeypatch.setattr("packet_ask.scope.os.set_blocking", interrupt)
    with pytest.raises(KeyboardInterrupt):
        collect_git_diff(tmp_path, unstaged=True)
    assert holder["proc"].poll() is not None
