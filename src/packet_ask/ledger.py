"""보낸 범위를 남기는 opt-in append-only 대장.

왜 필요한가: 영수증은 stderr 로 한 번 출력되고 사라진다. 스킬 배포에서
`--files` 를 고르는 것은 MAIN 에이전트이므로, 사람이 나중에 "내 에이전트가
무엇을 내보냈나"를 물을 표면이 없다. 이 파일이 그 표면이다.

payload 는 절대 남기지 않는다. 질문과 파일 본문은 기록 대상이 아니다.
"""

from __future__ import annotations

import errno
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.text import message

_LEDGER_ENV = "PACKET_ASK_LEDGER"
# 경로 25개 + 고정 필드로도 충분한 상한. 넘으면 기록 대신 실패한다.
MAX_LEDGER_LINE_BYTES = 64 * 1024
# 조상 탐색이 이상한 마운트에서 끝나지 않는 일이 없게 한다.
_MAX_ANCESTOR_WALK = 64


def ledger_path() -> Path | None:
    """설정된 대장 경로. 설정하지 않으면 기능 자체가 꺼져 있다."""
    raw = os.environ.get(_LEDGER_ENV, "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise PacketAskError(message("ledger_absolute"), codes.CONFINEMENT)
    return path


def build_ledger_entry(mode: str, receipt: dict[str, Any]) -> dict[str, Any]:
    """영수증에서 비밀 값 없는 필드만 골라 한 줄을 만든다.

    receipt 전체를 복사하지 않는다. receipt 가 나중에 민감한 필드를 갖더라도
    이름으로 고르는 구조라 대장으로 흐르지 않는다.
    """
    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "mode": mode,
        "provider": receipt["provider"],
        "selector": receipt["selector"],
        "paths": [str(path) for path in receipt["paths"]],
        "bytes": int(receipt["bytes"]),
        "sha256_packet_md": str(receipt["sha256_packet_md"]),
        # 하위 객체를 통째로 복사하면 redaction 이 나중에 라벨을 갖게 될 때
        # 그대로 새는 통로가 된다. 정수 count 만 남긴다.
        "redaction": {
            key: int(value)
            for key, value in receipt["redaction"].items()
            if isinstance(value, int) and not isinstance(value, bool)
        },
    }
    for key in ("timeout_seconds", "timeout_source", "timeout_applies"):
        if key in receipt:
            entry[key] = receipt[key]
    return entry


def append_ledger_entry(entry: dict[str, Any], worktree: Path | None) -> None:
    """대장이 켜져 있으면 한 줄을 덧붙인다. 실패하면 벤더를 실행하지 않는다."""
    path = ledger_path()
    if path is None:
        return
    _reject_ledger_inside_tree(path, worktree)
    line = json.dumps(entry, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    payload = (line + "\n").encode("utf-8")
    if len(payload) > MAX_LEDGER_LINE_BYTES:
        raise PacketAskError(message("ledger_line_bytes"), codes.CONFINEMENT)
    _append_private_line(path, payload)


def _reject_ledger_inside_tree(path: Path, worktree: Path | None) -> None:
    """워크트리 안의 대장은 스스로 packet 범위에 들어가므로 거절한다.

    경로 문자열 비교는 macOS 기본 APFS 처럼 대소문자를 구분하지 않는
    파일시스템에서 조용히 뚫린다. `/x/REPO` 는 `/x/repo` 의 relative_to 를
    통과하지만 같은 디렉터리다. device+inode 로 조상을 훑어 실제 동일성을 본다.
    """
    if worktree is None:
        return
    try:
        tree = worktree.resolve().stat()
    except OSError:
        raise PacketAskError(message("ledger_worktree"), codes.CONFINEMENT) from None
    tree_id = (tree.st_dev, tree.st_ino)
    probe = path.parent
    seen = 0
    while seen < _MAX_ANCESTOR_WALK:
        seen += 1
        try:
            resolved = probe.resolve()
            info = resolved.stat()
        except OSError:
            # 아직 없는 디렉터리는 건너뛰고 존재하는 조상까지 올라간다.
            parent = probe.parent
            if parent == probe:
                return
            probe = parent
            continue
        if (info.st_dev, info.st_ino) == tree_id:
            raise PacketAskError(message("ledger_worktree"), codes.CONFINEMENT)
        if resolved.parent == resolved:
            return
        probe = resolved.parent
    raise PacketAskError(message("ledger_worktree"), codes.CONFINEMENT)


def _append_private_line(path: Path, payload: bytes) -> None:
    """0600 으로 열고 O_APPEND 로 한 번에 쓴다. 심링크는 거절한다."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_NOFOLLOW 가 심링크를, O_NONBLOCK 이 리더 없는 FIFO 의 open 블로킹을 막는다.
        flags = (
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        descriptor = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PacketAskError(message("ledger_symlink"), codes.CONFINEMENT) from exc
        raise PacketAskError(message("ledger_write"), codes.CONFINEMENT) from exc
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid():
            raise PacketAskError(message("ledger_owner"), codes.CONFINEMENT)
        if not stat.S_ISREG(info.st_mode):
            raise PacketAskError(message("ledger_write"), codes.CONFINEMENT)
        # O_CREAT 의 mode 는 생성 때만 쓰인다. 이미 있던 0644 파일이면 그대로
        # 열리므로 파일명 이력이 월드 리더블로 쌓인다. 쓰기 전에 강제한다.
        if stat.S_IMODE(info.st_mode) != 0o600:
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        _write_all(descriptor, payload)
    except PacketAskError:
        raise
    except OSError as exc:
        raise PacketAskError(message("ledger_write"), codes.CONFINEMENT) from exc
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    """os.write 는 짧게 쓸 수 있다. 줄이 잘리면 대장 자체를 못 믿는다."""
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise PacketAskError(message("ledger_write"), codes.CONFINEMENT)
        written += count
