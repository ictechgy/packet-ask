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


def ledger_path() -> Path | None:
    """설정된 대장 경로. 설정하지 않으면 기능 자체가 꺼져 있다."""
    raw = os.environ.get(_LEDGER_ENV, "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise PacketAskError(message("ledger_absolute"), codes.CONFINEMENT)
    return path


def build_ledger_entry(
    mode: str,
    receipt: dict[str, Any],
    exit_code: int | None = None,
) -> dict[str, Any]:
    """영수증에서 비밀 값 없는 필드만 골라 한 줄을 만든다."""
    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": mode,
        "provider": receipt["provider"],
        "selector": receipt["selector"],
        "paths": [str(path) for path in receipt["paths"]],
        "bytes": int(receipt["bytes"]),
        "sha256_packet_md": str(receipt["sha256_packet_md"]),
        "redaction": dict(receipt["redaction"]),
    }
    for key in ("timeout_seconds", "timeout_source", "timeout_applies"):
        if key in receipt:
            entry[key] = receipt[key]
    if exit_code is not None:
        entry["exit_code"] = int(exit_code)
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
    """워크트리 안의 대장은 스스로 packet 범위에 들어가므로 거절한다."""
    if worktree is None:
        return
    try:
        path.resolve().relative_to(worktree.resolve())
    except ValueError:
        return
    raise PacketAskError(message("ledger_worktree"), codes.CONFINEMENT)


def _append_private_line(path: Path, payload: bytes) -> None:
    """0600 으로 열고 O_APPEND 로 한 번에 쓴다. 심링크는 거절한다."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise PacketAskError(message("ledger_symlink"), codes.CONFINEMENT)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
        descriptor = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    except PacketAskError:
        raise
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EMLINK}:
            raise PacketAskError(message("ledger_symlink"), codes.CONFINEMENT) from exc
        raise PacketAskError(message("ledger_write"), codes.CONFINEMENT) from exc
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid():
            raise PacketAskError(message("ledger_owner"), codes.CONFINEMENT)
        if not stat.S_ISREG(info.st_mode):
            raise PacketAskError(message("ledger_write"), codes.CONFINEMENT)
        os.write(descriptor, payload)
    except PacketAskError:
        raise
    except OSError as exc:
        raise PacketAskError(message("ledger_write"), codes.CONFINEMENT) from exc
    finally:
        os.close(descriptor)
