"""사람이 커밋한 공개 표면 선언.

왜 필요한가: 설계는 "사용자가 의도적으로 고른 패킷"이지만, 스킬 배포에서
`--files` 를 고르는 것은 MAIN 에이전트다. 명시 스코프 플래그는 "실수로 워킹
트리 전체"만 막고 "에이전트가 고른 256KiB"는 그대로 나간다.

이 파일은 선택을 막지 않는다. 범위를 넓히려면 커밋된 선언 파일을 고쳐야 하고,
그 편집은 `git status` 와 diff 에 나타나 기존 사람 리뷰 루프 위로 올라온다.
유출 방지 allowlist 가 아니라 공개 범위 선언이다.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path, PurePosixPath

from packet_ask.errors import ScopeError
from packet_ask.text import message

SURFACE_FILENAME = ".packet-ask-surface"
MAX_SURFACE_BYTES = 64 * 1024
MAX_SURFACE_ENTRIES = 1000


def load_surface(worktree: Path) -> tuple[str, ...] | None:
    """선언 파일을 읽는다. 없으면 None 이고 강제는 꺼져 있다."""
    path = worktree / SURFACE_FILENAME
    try:
        # O_NOFOLLOW 로 열어 검사와 사용 사이의 심링크 교체를 없앤다.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ScopeError(message("surface_symlink")) from exc
        raise ScopeError(message("surface_encoding")) from exc
    try:
        info = os.fstat(descriptor)
        if stat.S_ISDIR(info.st_mode):
            return None
        if not stat.S_ISREG(info.st_mode):
            raise ScopeError(message("surface_symlink"))
        # 상한 밖은 읽지 않는다. 커밋된 거대 파일을 통째로 메모리에 올리지 않는다.
        raw = os.read(descriptor, MAX_SURFACE_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_SURFACE_BYTES:
        raise ScopeError(message("surface_bytes"))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScopeError(message("surface_encoding")) from exc
    return _parse_surface(text)


def _parse_surface(text: str) -> tuple[str, ...]:
    """빈 줄과 주석을 걸러 정규 상대 경로 접두어만 남긴다."""
    entries: list[str] = []
    # BOM 은 strip() 대상이 아니다. 남겨두면 첫 선언이 조용히 죽는다.
    for line in text.lstrip("\ufeff").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        entries.append(_normalize_entry(entry))
        if len(entries) > MAX_SURFACE_ENTRIES:
            raise ScopeError(message("surface_entries"))
    if not entries:
        raise ScopeError(message("surface_empty"))
    return tuple(entries)


def _normalize_entry(entry: str) -> str:
    """절대 경로·상위 참조·제어문자·글롭을 거절한다."""
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in entry):
        raise ScopeError(message("surface_entry"))
    # 글롭은 뜻이 모호하고 조용히 넓어진다. 접두어만 받는다.
    if any(char in entry for char in "*?[]"):
        raise ScopeError(message("surface_entry"))
    pure = PurePosixPath(entry)
    if pure.is_absolute() or ".." in pure.parts:
        raise ScopeError(message("surface_entry"))
    normalized = "/".join(part for part in pure.parts if part not in {"", "."})
    if not normalized:
        raise ScopeError(message("surface_entry"))
    return normalized


def assert_within_surface(relatives: list[str], surface: tuple[str, ...]) -> None:
    """선언 밖 경로를 하나라도 고르면 거절한다."""
    outside = [item for item in relatives if not _is_declared(item, surface)]
    if outside:
        raise ScopeError(message("surface_outside"))


def _is_declared(relative: str, surface: tuple[str, ...]) -> bool:
    """경로가 선언된 접두어와 같거나 그 아래인지 본다. 문자열 접두어가 아니다."""
    parts = PurePosixPath(relative).parts
    # 호출자가 이미 정규화하지만 이 모듈이 자기 불변식을 스스로 지킨다.
    if any(part in {".", ".."} for part in parts) or PurePosixPath(relative).is_absolute():
        return False
    for entry in surface:
        declared = PurePosixPath(entry).parts
        if parts[: len(declared)] == declared:
            return True
    return False
