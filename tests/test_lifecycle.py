"""packet lease와 오래된 cache 정리 경계."""

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path

import pytest

from packet_ask import lifecycle
from packet_ask.errors import PacketAskError
from packet_ask.lifecycle import (
    PACKET_LEASE_NAME,
    STALE_PACKET_SECONDS,
    close_packet_lease,
    reap_stale_packets,
)
from packet_ask.packet import Packet, build_packet


def _release(packet: Packet) -> None:
    """crash 뒤 OS가 lease fd를 닫은 상태를 테스트에서 만든다."""
    close_packet_lease(packet._lease_fd)
    packet._lease_fd = None


def _make_old(path: Path, now: float) -> None:
    old = now - STALE_PACKET_SECONDS - 1
    os.utime(path, (old, old))


def test_packet_lease_is_private_and_exclusive(tmp_path: Path) -> None:
    """살아 있는 packet은 current process가 잡은 0600 lease를 가진다."""
    packet = build_packet("review", "review", [], None, tmp_path)
    lease = packet.root / PACKET_LEASE_NAME
    assert stat.S_IMODE(lease.stat().st_mode) == 0o600
    assert packet._lease_fd is not None
    assert os.get_inheritable(packet._lease_fd) is False
    second = os.open(packet.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(second)
        packet.destroy()


def test_reap_removes_old_unlocked_packet(tmp_path: Path) -> None:
    """crash로 lease가 풀린 뒤 24시간이 지난 packet만 제거한다."""
    now = 2_000_000_000.0
    packet = build_packet("review", "review", [], None, tmp_path)
    root = packet.root
    _release(packet)
    _make_old(root / PACKET_LEASE_NAME, now)
    assert reap_stale_packets(tmp_path, now=now) == 1
    assert not root.exists()


def test_reap_keeps_active_packet_even_when_old(tmp_path: Path) -> None:
    """오래된 mtime도 active lease가 있으면 동시 실행으로 보고 보존한다."""
    now = 2_000_000_000.0
    packet = build_packet("review", "review", [], None, tmp_path)
    _make_old(packet.root / PACKET_LEASE_NAME, now)
    assert reap_stale_packets(tmp_path, now=now) == 0
    assert packet.root.exists()
    packet.destroy()


def test_reap_keeps_fresh_unlocked_packet(tmp_path: Path) -> None:
    """lease가 풀려도 age threshold 전에는 삭제하지 않는다."""
    packet = build_packet("review", "review", [], None, tmp_path)
    lease = packet.root / PACKET_LEASE_NAME
    now = lease.stat().st_mtime + STALE_PACKET_SECONDS - 1
    _release(packet)
    assert reap_stale_packets(tmp_path, now=now) == 0
    assert packet.root.exists()
    packet.destroy()


def test_reap_skips_legacy_and_nonprivate_candidates(tmp_path: Path) -> None:
    """도구 lease가 없거나 private mode가 아닌 이름만 같은 경로는 삭제하지 않는다."""
    legacy = tmp_path / "packet-ask-legacy"
    legacy.mkdir(mode=0o700)
    nonprivate = tmp_path / "packet-ask-nonprivate"
    nonprivate.mkdir(mode=0o700)
    lease = nonprivate / PACKET_LEASE_NAME
    lease.write_text("", encoding="utf-8")
    lease.chmod(0o644)
    assert reap_stale_packets(tmp_path, now=2_000_000_000.0, stale_after=0) == 0
    assert legacy.is_dir()
    assert nonprivate.is_dir()


def test_reap_never_follows_packet_symlink(tmp_path: Path) -> None:
    """prefix symlink는 외부 target이 오래되어도 후보로 열지 않는다."""
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    lease = outside / PACKET_LEASE_NAME
    lease.write_text("", encoding="utf-8")
    lease.chmod(0o600)
    link = tmp_path / "packet-ask-link"
    link.symlink_to(outside, target_is_directory=True)
    assert reap_stale_packets(tmp_path, now=2_000_000_000.0, stale_after=0) == 0
    assert marker.read_text(encoding="utf-8") == "keep"


def test_reap_scan_failure_is_stable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cache scan 자체가 실패하면 새 packet을 만들기 전에 안정된 오류로 닫는다."""
    monkeypatch.setattr(
        "packet_ask.lifecycle.os.scandir",
        lambda _path: (_ for _ in ()).throw(OSError("blocked")),
    )
    with pytest.raises(PacketAskError, match="stale temporary packets"):
        reap_stale_packets(tmp_path)


def test_reap_does_not_delete_active_replacement_after_inode_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """검사 뒤 이름이 active packet으로 바뀌어도 열린 stale inode만 비운다."""
    now = 2_000_000_000.0
    stale = build_packet("review", "stale", [], None, tmp_path)
    active = build_packet("review", "active", [], None, tmp_path)
    stale_root = stale.root
    active_root = active.root
    parked_stale = tmp_path / "parked-stale"
    _release(stale)
    _make_old(stale_root / PACKET_LEASE_NAME, now)
    real_clear = lifecycle._clear_directory
    swapped = False

    def swap_then_clear(directory_fd: int, preserve: set[str] | None = None) -> None:
        nonlocal swapped
        if preserve and not swapped:
            stale_root.rename(parked_stale)
            active_root.rename(stale_root)
            swapped = True
        real_clear(directory_fd, preserve)

    monkeypatch.setattr(lifecycle, "_clear_directory", swap_then_clear)
    assert reap_stale_packets(tmp_path, now=now) == 1
    assert (stale_root / "packet.md").is_file()
    assert "active" in (stale_root / "packet.md").read_text(encoding="utf-8")
    assert list(parked_stale.iterdir()) == []

    stale_root.rename(active_root)
    parked_stale.rmdir()
    active.destroy()


def test_partial_packet_cleanup_keeps_lease_for_later_gc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """내용 삭제가 실패하면 marker를 남겨 다음 GC가 scrubbed tree를 재시도한다."""
    packet = build_packet("review", "review", [], None, tmp_path)
    root = packet.root
    real_unlink = lifecycle.os.unlink

    def fail_packet_md(path: str, *, dir_fd: int | None = None) -> None:
        if path == "packet.md":
            raise OSError("blocked")
        real_unlink(path, dir_fd=dir_fd)

    with monkeypatch.context() as patch:
        patch.setattr(lifecycle.os, "unlink", fail_packet_md)
        with pytest.raises(OSError, match="blocked"):
            packet.destroy()

    lease = root / PACKET_LEASE_NAME
    assert root.is_dir()
    assert lease.is_file()
    now = 2_000_000_000.0
    _make_old(lease, now)
    assert reap_stale_packets(tmp_path, now=now) == 1
    assert not root.exists()
