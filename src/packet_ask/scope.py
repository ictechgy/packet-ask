"""git 워크트리 안에서만 파일과 diff를 모은다."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from packet_ask.errors import BudgetError, ScopeError
from packet_ask.paths import resolve_trusted_executable, trusted_path_value

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
# tokenizer.py 같은 부분문자열은 빼고, token/secret 을 경로 조각으로만 본다.
_SECRET_SEGMENT_RE = re.compile(
    r"(?i)(?:^|[._-])(?:token|secret|password|credential|passwd)(?:[._-]|$)"
)


@dataclass(frozen=True)
class ScopedFile:
    """워크트리 상대경로와 텍스트 내용."""

    relative: str
    content: str


def resolve_worktree(start: Path) -> Path:
    """start에서 git 최상위 디렉터리를 찾는다."""
    result = subprocess.run(
        [_git_executable(), "rev-parse", "--show-toplevel"],
        cwd=start,
        check=False,
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    if result.returncode != 0:
        raise ScopeError("git 워크트리가 아닙니다.")
    return Path(result.stdout.strip()).resolve()


def is_vcs_path(path: Path) -> bool:
    """.git 메타데이터 경로인지 본다."""
    return ".git" in path.parts


def is_secret_path(path: Path) -> bool:
    """시크릿으로 보이는 파일명을 가린다. token 부분문자열만으로는 맞추지 않는다."""
    return any(_name_is_secret(part) for part in path.parts)


def _name_is_secret(name: str) -> bool:
    """경로 한 조각이 시크릿 파일명 규칙에 맞는지 본다."""
    lowered = name.lower()
    if lowered in _SECRET_NAMES or lowered.startswith(".env"):
        return True
    if any(lowered.endswith(suffix) for suffix in _SECRET_SUFFIXES):
        return True
    return _SECRET_SEGMENT_RE.search(name) is not None


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
        relative = real.relative_to(worktree)
        _reject_blocked_relative(relative)
        data = real.read_bytes()
        total += len(data)
        if total > max_bytes:
            raise BudgetError(f"총 용량이 {max_bytes}바이트를 넘습니다.")
        collected.append(ScopedFile(relative=relative.as_posix(), content=data.decode("utf-8", errors="replace")))
    return collected


def _reject_blocked_relative(relative: Path) -> None:
    """git 메타데이터와 시크릿 파일명을 거절한다."""
    if is_vcs_path(relative):
        raise ScopeError(f"git 메타데이터는 보낼 수 없습니다: {relative.as_posix()}")
    if is_secret_path(relative):
        raise ScopeError(f"시크릿 파일명은 보낼 수 없습니다: {relative.name}")


def _git_executable() -> str:
    """신뢰 경로의 git 만 쓴다."""
    found = resolve_trusted_executable("git")
    if found is None:
        raise ScopeError("신뢰 경로에서 git 을 찾지 못했습니다.")
    return str(found)


def _git_env() -> dict[str, str]:
    """글로벌 git 설정과 외부 diff 훅을 타지 않는 최소 환경."""
    return {
        "PATH": trusted_path_value(),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "LANG": "C",
        "LC_ALL": "C",
    }


def _git_range_args(
    range_spec: str | None,
    unstaged: bool,
    staged: bool,
) -> list[str]:
    """사용자 문자열을 옵션으로 쓰지 않고 diff 범위만 고른다."""
    if range_spec is not None:
        if range_spec.startswith("-"):
            raise ScopeError("잘못된 diff 범위입니다.")
        return [range_spec]
    if staged:
        return ["--cached"]
    if unstaged:
        return []
    raise ScopeError("diff 범위를 지정하세요.")


def _run_git(worktree: Path, extra: list[str]) -> str:
    """git 을 셸 없이 실행한다."""
    command = [_git_executable(), *extra]
    result = subprocess.run(
        command,
        cwd=worktree,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_git_env(),
    )
    if result.returncode != 0:
        raise ScopeError("git 명령을 실행하지 못했습니다.")
    return result.stdout


def _diff_guard_args() -> list[str]:
    """외부 diff 와 textconv 를 끈다."""
    return [
        "-c",
        "diff.noprefix=false",
        "-c",
        "diff.mnemonicPrefix=false",
        "-c",
        "core.quotepath=false",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
    ]


def _name_status_paths(worktree: Path, range_args: list[str]) -> list[str]:
    """NUL 구분 name-status 로 원본·대상 경로를 모두 모은다."""
    raw = _run_git(worktree, [*_diff_guard_args(), "--name-status", "-z", *range_args])
    return _parse_name_status(raw)


def _parse_name_status(raw: str) -> list[str]:
    """git diff --name-status -z 출력을 경로 목록으로 푼다."""
    parts = [part for part in raw.split("\0") if part != ""]
    paths: list[str] = []
    index = 0
    while index < len(parts):
        status = parts[index]
        if status[:1] in {"R", "C"}:
            paths.extend(parts[index + 1 : index + 3])
            index += 3
            continue
        if index + 1 >= len(parts):
            raise ScopeError("git name-status 출력을 해석하지 못했습니다.")
        paths.append(parts[index + 1])
        index += 2
    return paths


def _reject_oversized_diff(text: str, max_bytes: int) -> None:
    """diff 용량 예산을 넘기면 거절한다."""
    if len(text.encode("utf-8")) > max_bytes:
        raise BudgetError(f"총 용량이 {max_bytes}바이트를 넘습니다.")


def _reject_secret_diff_paths(paths: list[str]) -> None:
    """시크릿·git 경로는 일부를 지우는 대신 전체를 거절한다."""
    for relative in paths:
        path = Path(relative)
        if is_vcs_path(path) or is_secret_path(path):
            raise ScopeError(f"시크릿 또는 git 경로가 diff에 있습니다: {path.name}")


def collect_git_diff(
    worktree: Path,
    range_spec: str | None = None,
    unstaged: bool = False,
    staged: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> str:
    """요청한 diff를 텍스트로 모은다. 사용자 문자열을 셸에 넣지 않는다."""
    range_args = _git_range_args(range_spec, unstaged, staged)
    paths = _name_status_paths(worktree, range_args)
    text = _run_git(worktree, [*_diff_guard_args(), *range_args])
    if not text.strip():
        raise ScopeError("범위에 변경이 없습니다.")
    if not paths:
        raise ScopeError("diff 본문은 있는데 경로를 읽지 못했습니다.")
    _reject_oversized_diff(text, max_bytes)
    _reject_secret_diff_paths(paths)
    return text

