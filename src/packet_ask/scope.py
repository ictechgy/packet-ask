"""git 워크트리 안에서만 파일과 diff를 모은다."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from packet_ask.errors import BudgetError, ScopeError

DEFAULT_MAX_FILES = 25
DEFAULT_MAX_BYTES = 256 * 1024

_SECRET_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".env",
    ".npmrc",
    ".pypirc",
    ".netrc",
)
_SECRET_NAMES = {
    "id_rsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
    ".env",
}


@dataclass(frozen=True)
class ScopedFile:
    """워크트리 상대경로와 텍스트 내용."""

    relative: str
    content: str


def resolve_worktree(start: Path) -> Path:
    """start에서 git 최상위 디렉터리를 찾는다."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ScopeError("git 워크트리가 아닙니다.")
    return Path(result.stdout.strip()).resolve()


def is_secret_path(path: Path) -> bool:
    """시크릿으로 보이는 파일명을 가린다."""
    name = path.name.lower()
    if name in _SECRET_NAMES or name.startswith(".env"):
        return True
    if "credential" in name or "token" in name:
        return True
    return any(name.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def _must_stay_inside(worktree: Path, candidate: Path) -> Path:
    """심링크를 푼 뒤 워크트리 안에 있는지 확인한다."""
    real = candidate.resolve()
    try:
        real.relative_to(worktree)
    except ValueError as exc:
        raise ScopeError(f"워크트리 밖 경로입니다: {candidate}") from exc
    if real.is_symlink() or candidate.is_symlink():
        raise ScopeError(f"심링크는 허용하지 않습니다: {candidate}")
    if not real.is_file():
        raise ScopeError(f"일반 파일이 아닙니다: {candidate}")
    return real


def collect_files(
    worktree: Path,
    paths: list[Path],
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> list[ScopedFile]:
    """지정 파일을 읽어 상대경로 목록으로 반환한다."""
    worktree = worktree.resolve()
    if len(paths) > max_files:
        raise BudgetError(f"파일 수가 {max_files}개를 넘습니다.")
    collected: list[ScopedFile] = []
    total = 0
    for raw in paths:
        real = _must_stay_inside(worktree, raw)
        if is_secret_path(real):
            raise ScopeError(f"시크릿 파일명은 보낼 수 없습니다: {real.name}")
        data = real.read_bytes()
        total += len(data)
        if total > max_bytes:
            raise BudgetError(f"총 용량이 {max_bytes}바이트를 넘습니다.")
        relative = real.relative_to(worktree).as_posix()
        collected.append(ScopedFile(relative=relative, content=data.decode("utf-8", errors="replace")))
    return collected


def _run_git_diff(worktree: Path, args: list[str]) -> str:
    """git diff를 셸 없이 실행한다."""
    command = ["git", "diff", "--no-ext-diff", *args]
    result = subprocess.run(
        command,
        cwd=worktree,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ScopeError("git diff 를 실행하지 못했습니다.")
    return result.stdout


def collect_git_diff(
    worktree: Path,
    range_spec: str | None = None,
    unstaged: bool = False,
    staged: bool = False,
) -> str:
    """요청한 diff를 텍스트로 모은다. 사용자 문자열을 셸에 넣지 않는다."""
    if range_spec is not None:
        if range_spec.startswith("-") or ".." not in range_spec and "..." not in range_spec:
            # 단일 SHA도 허용하되 옵션 주입은 막는다.
            if range_spec.startswith("-"):
                raise ScopeError("잘못된 diff 범위입니다.")
        text = _run_git_diff(worktree, [range_spec])
    elif staged:
        text = _run_git_diff(worktree, ["--cached"])
    elif unstaged:
        text = _run_git_diff(worktree, [])
    else:
        raise ScopeError("diff 범위를 지정하세요.")
    if not text.strip():
        raise ScopeError("범위에 변경이 없습니다.")
    return text
