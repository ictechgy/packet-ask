"""git 워크트리 안에서만 파일과 diff를 모은다."""

from __future__ import annotations

import os
import re
import select
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from packet_ask.errors import BudgetError, ScopeError
from packet_ask.paths import git_subprocess_env, resolve_trusted_executable
from packet_ask.text import message

DEFAULT_MAX_FILES = 25
DEFAULT_MAX_BYTES = 256 * 1024
GIT_TIMEOUT_SECONDS = 30
GIT_METADATA_OUTPUT_BYTES = 4096

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
    r"(?i)(?:^|[._-])(?:token|secret|password|credential|passwd)s?(?:[._-]|$)"
)


@dataclass(frozen=True)
class ScopedFile:
    """워크트리 상대경로와 텍스트 내용."""

    relative: str
    content: str


def resolve_worktree(start: Path) -> Path:
    """start에서 git 최상위 디렉터리를 찾는다."""
    try:
        root = run_bounded_git(
            start,
            ["rev-parse", "--show-toplevel"],
            GIT_METADATA_OUTPUT_BYTES,
        ).strip()
    except (BudgetError, ScopeError) as exc:
        raise ScopeError(message("not_worktree")) from exc
    if not root or len(root.encode("utf-8")) > GIT_METADATA_OUTPUT_BYTES:
        raise ScopeError(message("not_worktree"))
    return Path(root).resolve()


def is_vcs_path(path: Path) -> bool:
    """.git 메타데이터 경로인지 본다. 대소문자를 가리지 않는다."""
    return any(part.lower() == ".git" for part in path.parts)


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
        raise ScopeError(message("outside_worktree", name=candidate)) from exc
    if real.is_symlink() or candidate.is_symlink():
        raise ScopeError(message("symlink_path", name=candidate))
    if not real.is_file():
        raise ScopeError(message("regular_file", name=candidate))
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
        raise BudgetError(message("max_files", limit=max_files))
    collected: list[ScopedFile] = []
    total = 0
    for raw in paths:
        real = _must_stay_inside(worktree, raw)
        relative = real.relative_to(worktree)
        _reject_blocked_relative(relative)
        remaining = max_bytes - total
        if remaining < 0:
            raise BudgetError(message("max_bytes", limit=max_bytes))
        data = _read_text_file_bounded(real, remaining)
        total += len(data)
        if total > max_bytes:
            raise BudgetError(message("max_bytes", limit=max_bytes))
        collected.append(ScopedFile(relative=relative.as_posix(), content=data.decode("utf-8")))
    return collected


def _read_text_file_bounded(path: Path, max_bytes: int) -> bytes:
    """파일을 max_bytes + 1까지만 읽고 바이너리·비 UTF-8을 거절한다."""
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise BudgetError(message("max_bytes", limit=max_bytes))
    if b"\x00" in data:
        raise ScopeError(message("binary_file", name=path.name))
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScopeError(message("utf8_file", name=path.name)) from exc
    return data


def _reject_blocked_relative(relative: Path) -> None:
    """git 메타데이터와 시크릿 파일명을 거절한다."""
    if is_vcs_path(relative):
        raise ScopeError(message("vcs_path", name=relative.as_posix()))
    if is_secret_path(relative):
        raise ScopeError(message("secret_path", name=relative.name))


def _git_executable() -> str:
    """신뢰 경로의 git 만 쓴다."""
    found = resolve_trusted_executable("git")
    if found is None:
        raise ScopeError(message("missing_git"))
    return str(found)


def _git_env() -> dict[str, str]:
    """글로벌 git 설정과 외부 diff 훅을 타지 않는 최소 환경."""
    return git_subprocess_env()


def _git_range_args(
    range_spec: str | None,
    unstaged: bool,
    staged: bool,
) -> list[str]:
    """사용자 문자열을 옵션으로 쓰지 않고 diff 범위만 고른다."""
    if range_spec is not None:
        if range_spec.startswith("-"):
            raise ScopeError(message("invalid_diff_range"))
        return [range_spec]
    if staged:
        return ["--cached"]
    if unstaged:
        return []
    raise ScopeError(message("diff_required"))


def _stop_process_group(proc: subprocess.Popen[bytes]) -> None:
    """bounded git 실행을 그룹 단위로 끝낸다."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass


def run_bounded_git(worktree: Path, extra: list[str], max_bytes: int) -> str:
    """git stdout을 제한·timeout 아래에서 셸 없이 읽는다."""
    command = [_git_executable(), *extra]
    try:
        proc = subprocess.Popen(
            command,
            cwd=worktree,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_env(),
            start_new_session=True,
        )
    except OSError as exc:
        raise ScopeError(message("git_failed")) from exc
    try:
        if proc.stdout is None:
            raise ScopeError(message("git_output_failed"))
        os.set_blocking(proc.stdout.fileno(), False)
        deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise ScopeError(message("git_timeout"))
            ready, _, _ = select.select([proc.stdout], [], [], min(0.1, remaining_time))
            if not ready:
                continue
            try:
                chunk = os.read(proc.stdout.fileno(), min(65536, max_bytes - total + 1))
            except BlockingIOError:
                continue
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise BudgetError(message("max_bytes", limit=max_bytes))
        try:
            returncode = proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            raise ScopeError(message("git_exit_timeout")) from None
        if returncode != 0:
            raise ScopeError(message("git_failed"))
        text = b"".join(chunks).decode("utf-8", errors="replace")
        _reject_oversized_diff(text, max_bytes)
        return text
    except BaseException:
        _stop_process_group(proc)
        raise


def _diff_guard_args() -> list[str]:
    """외부 diff 와 textconv 를 끈다."""
    return [
        "-c",
        "diff.noprefix=false",
        "-c",
        "diff.mnemonicPrefix=false",
        "-c",
        "core.quotepath=false",
        "-c",
        "core.fsmonitor=false",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
    ]


def _name_status_paths(worktree: Path, range_args: list[str], max_bytes: int) -> list[str]:
    """NUL 구분 name-status 로 원본·대상 경로를 모두 모은다."""
    raw = run_bounded_git(
        worktree,
        [*_diff_guard_args(), "--name-status", "-z", *range_args],
        max_bytes,
    )
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
            raise ScopeError(message("name_status_parse"))
        paths.append(parts[index + 1])
        index += 2
    return paths


def _reject_oversized_diff(text: str, max_bytes: int) -> None:
    """diff 용량 예산을 넘기면 거절한다."""
    if len(text.encode("utf-8")) > max_bytes:
        raise BudgetError(message("max_bytes", limit=max_bytes))


def _reject_secret_diff_paths(paths: list[str]) -> None:
    """시크릿·git 경로는 일부를 지우는 대신 전체를 거절한다."""
    for relative in paths:
        path = Path(relative)
        if is_vcs_path(path) or is_secret_path(path):
            raise ScopeError(message("secret_path", name=path.name))


def collect_git_diff(
    worktree: Path,
    range_spec: str | None = None,
    unstaged: bool = False,
    staged: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
) -> str:
    """요청한 diff를 텍스트로 모은다. 사용자 문자열을 셸에 넣지 않는다."""
    if max_bytes < 1:
        raise BudgetError(message("max_bytes", limit=max_bytes))
    if max_files < 1:
        raise BudgetError(message("max_files", limit=max_files))
    range_args = _git_range_args(range_spec, unstaged, staged)
    paths = _name_status_paths(worktree, range_args, max_bytes)
    if len(set(paths)) > max_files:
        raise BudgetError(message("max_files", limit=max_files))
    text = run_bounded_git(worktree, [*_diff_guard_args(), *range_args], max_bytes)
    if not text.strip():
        raise ScopeError(message("empty_diff"))
    if not paths:
        raise ScopeError(message("missing_diff_paths"))
    _reject_oversized_diff(text, max_bytes)
    _reject_secret_diff_paths(paths)
    return text
