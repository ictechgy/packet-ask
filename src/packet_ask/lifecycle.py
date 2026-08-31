"""crash 뒤 남은 packet cache를 lease로 구분해 제한적으로 정리한다."""

from __future__ import annotations

import errno
import fcntl
import os
import stat
import time
from pathlib import Path

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.text import message

PACKET_DIR_PREFIX = "packet-ask-"
PACKET_LEASE_NAME = ".lease"
STALE_PACKET_SECONDS = 24 * 60 * 60


def create_packet_lease(root: Path) -> int:
    """0600 marker를 만들고 root directory에 exclusive lease를 잡는다."""
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    marker_fd: int | None = None
    directory_fd: int | None = None
    try:
        marker_fd = os.open(root / PACKET_LEASE_NAME, flags, 0o600)
        os.fchmod(marker_fd, 0o600)
        _close_descriptor(marker_fd)
        marker_fd = None
        directory_fd = _open_directory(root)
        fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return directory_fd
    except OSError as exc:
        if marker_fd is not None:
            _close_descriptor(marker_fd)
        if directory_fd is not None:
            _close_descriptor(directory_fd)
        raise PacketAskError(message("packet_lease_failed"), codes.INTERNAL) from exc


def close_packet_lease(descriptor: int | None) -> None:
    """process-local lease fd를 닫아 advisory lock을 해제한다."""
    if descriptor is not None:
        _close_descriptor(descriptor)


def remove_packet_tree(root: Path, *, directory_fd: int | None = None) -> None:
    """열린 directory fd 안의 내용을 지우고 lease marker를 마지막에 제거한다."""
    parent_fd = _open_directory(root.parent)
    root_fd = directory_fd
    owns_root_fd = root_fd is None
    try:
        if root_fd is None:
            root_fd = _open_directory(root.name, dir_fd=parent_fd)
        _clear_directory(root_fd, preserve={PACKET_LEASE_NAME})
        try:
            os.unlink(PACKET_LEASE_NAME, dir_fd=root_fd)
        except FileNotFoundError:
            pass
        os.rmdir(root.name, dir_fd=parent_fd)
    finally:
        if owns_root_fd and root_fd is not None:
            _close_descriptor(root_fd)
        _close_descriptor(parent_fd)


def reap_stale_packets(
    parent: Path,
    *,
    now: float | None = None,
    stale_after: int = STALE_PACKET_SECONDS,
) -> int:
    """현재 uid가 소유한 오래되고 unlock된 packet child만 제거한다."""
    current_time = time.time() if now is None else now
    parent_fd: int | None = None
    reaped = 0
    try:
        parent_fd = _open_directory(parent)
        with os.scandir(parent_fd) as entries:
            for entry in entries:
                if not entry.name.startswith(PACKET_DIR_PREFIX):
                    continue
                if _reap_candidate(parent_fd, entry.name, current_time, stale_after):
                    reaped += 1
    except FileNotFoundError:
        return 0
    except PacketAskError:
        raise
    except OSError as exc:
        raise PacketAskError(message("packet_gc_failed"), codes.INTERNAL) from exc
    finally:
        if parent_fd is not None:
            _close_descriptor(parent_fd)
    return reaped


def _reap_candidate(parent_fd: int, name: str, now: float, stale_after: int) -> bool:
    """검증한 direct child의 lease를 잡은 동안에만 그 경로를 지운다."""
    directory_fd: int | None = None
    marker_fd: int | None = None
    try:
        directory_fd = _open_candidate_directory(parent_fd, name)
        if directory_fd is None:
            return False
        directory_info = os.fstat(directory_fd)
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise
        marker_fd = _open_candidate_lease(directory_fd)
        if marker_fd is None:
            return False
        lease_info = os.fstat(marker_fd)
        if not _private_owned_regular_file(lease_info):
            return False
        if now - lease_info.st_mtime < stale_after:
            return False
        if not _same_directory(parent_fd, name, directory_info):
            return False
        _clear_directory(directory_fd, preserve={PACKET_LEASE_NAME})
        try:
            os.unlink(PACKET_LEASE_NAME, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        try:
            os.rmdir(name, dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno not in {errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST}:
                raise
        return True
    except OSError as exc:
        if exc.errno in {
            errno.EACCES,
            errno.ENOENT,
            errno.ENOTDIR,
            errno.ELOOP,
            errno.EISDIR,
        }:
            return False
        raise PacketAskError(message("packet_gc_failed"), codes.INTERNAL) from exc
    finally:
        close_packet_lease(marker_fd)
        if directory_fd is not None:
            _close_descriptor(directory_fd)


def _open_candidate_directory(parent_fd: int, name: str) -> int | None:
    """symlink를 따르지 않고 private current-user 디렉터리만 연다."""
    descriptor = _open_directory(name, dir_fd=parent_fd)
    try:
        info = os.fstat(descriptor)
    except OSError:
        _close_descriptor(descriptor)
        raise
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        _close_descriptor(descriptor)
        return None
    return descriptor


def _open_candidate_lease(directory_fd: int) -> int | None:
    """candidate 내부 marker를 nonblocking·no-follow로 연다."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(PACKET_LEASE_NAME, flags, dir_fd=directory_fd)
    except OSError as exc:
        if exc.errno in {
            errno.EACCES,
            errno.ENOENT,
            errno.ENOTDIR,
            errno.ELOOP,
            errno.EISDIR,
        }:
            return None
        raise


def _private_owned_regular_file(info: os.stat_result) -> bool:
    """lease marker가 current-user 0600 regular file인지 본다."""
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o600
    )


def _same_directory(parent_fd: int, name: str, opened: os.stat_result) -> bool:
    """검사 뒤 path가 다른 inode로 교체되지 않았는지 다시 확인한다."""
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    return (
        stat.S_ISDIR(current.st_mode)
        and current.st_dev == opened.st_dev
        and current.st_ino == opened.st_ino
    )


def _clear_directory(directory_fd: int, preserve: set[str] | None = None) -> None:
    """열린 디렉터리 아래만 순회하며 symlink를 따라가지 않고 비운다."""
    preserved = preserve or set()
    for name in os.listdir(directory_fd):
        if name in preserved:
            continue
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            os.unlink(name, dir_fd=directory_fd)
            continue
        child_fd = _open_directory(name, dir_fd=directory_fd)
        try:
            opened = os.fstat(child_fd)
            if opened.st_dev != info.st_dev or opened.st_ino != info.st_ino:
                raise OSError(errno.ESTALE, "packet directory changed during cleanup")
            _clear_directory(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
        finally:
            _close_descriptor(child_fd)


def _open_directory(path: str | Path, *, dir_fd: int | None = None) -> int:
    """최종 symlink를 따르지 않고 directory fd를 연다."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags, dir_fd=dir_fd)


def _close_descriptor(descriptor: int) -> None:
    """cleanup 경로에서 이미 닫힌 fd 오류를 숨긴다."""
    try:
        os.close(descriptor)
    except OSError:
        pass
