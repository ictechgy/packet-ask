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
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.receipt import SCHEMA
from packet_ask.text import message

_LEDGER_ENV = "PACKET_ASK_LEDGER"
# 경로 25개 + 고정 필드로도 충분한 상한. 넘으면 기록 대신 실패한다.
MAX_LEDGER_LINE_BYTES = 64 * 1024
# 조상 탐색이 이상한 마운트에서 끝나지 않는 일이 없게 한다.
_MAX_ANCESTOR_WALK = 64

# 대장에는 두 종류의 줄이 있다. `egress` 는 "무엇이 나갔나", `result` 는
# "무엇이 돌아왔나" 다. 섞이면 두 질문 다 흐려지므로 줄마다 밝힌다. 0.11.0
# 이전 줄에는 `phase` 가 없고 전부 egress 다 — 읽는 쪽은 그것을 유지한다.
# 읽는 쪽 규칙은 둘이다: `phase` 가 없으면 egress 로 읽고, **모르는 phase 는
# 건너뛴다.** result 줄에는 `selector`·`paths` 가 없으므로 모든 줄을 egress 로
# 해석하면 KeyError 가 난다.
PHASE_EGRESS = "egress"
PHASE_RESULT = "result"

# 결과 줄의 결말. `answered` 는 벤더가 응답을 돌려주고 출력 가드를 통과했다는
# 뜻이지 프로세스가 0 으로 끝난다는 뜻이 아니다(cleanup 경고는 뒤에 올 수 있다).
# `not-observable` 은 paste 처럼 답이 이 도구를 지나지 않는 경우다.
RESULT_ANSWERED = "answered"
RESULT_FAILED = "failed"
RESULT_NOT_OBSERVABLE = "not-observable"
RESULT_OUTCOMES = frozenset({RESULT_ANSWERED, RESULT_FAILED, RESULT_NOT_OBSERVABLE})


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
    """영수증에서 비밀 값 없는 필드만 골라 egress 한 줄을 만든다.

    receipt 전체를 복사하지 않는다. receipt 가 나중에 민감한 필드를 갖더라도
    이름으로 고르는 구조라 대장으로 흐르지 않는다.
    """
    entry: dict[str, Any] = {
        "timestamp": _timestamp(),
        "phase": PHASE_EGRESS,
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
    for key in (
        "surface",
        "effort",
        "effort_source",
        "timeout_seconds",
        "timeout_source",
        "timeout_applies",
        "supervision",
    ):
        if key in receipt:
            entry[key] = receipt[key]
    return entry


def build_ledger_result(
    mode: str,
    receipt: dict[str, Any],
    outcome: str,
    output_bytes: int,
    output_hint: bool,
    failure_code: int | None = None,
) -> dict[str, Any]:
    """응답 쪽 한 줄을 만든다. 본문·질문·벤더 stderr 는 담지 않는다.

    짝 짓는 키는 packet digest 다. 새 실행 식별자를 만들지 않는다 — 같은
    패킷을 두 번 보내면 두 쌍이 생기고, 그것이 실제 일이다.

    어긋난 조합은 거절한다. `answered` 가 아닌데 출력 크기나 힌트가 채워지면
    paste 의 패킷 echo 를 "돌아온 답" 으로 기록한 것이므로 조용히 잘못된
    데이터가 쌓인다. `build_receipt` 의 effort/effort_source 검사와 같은
    이유로 사용자 메시지가 아니라 ValueError 다 — 호출자 쪽 프로그래밍 오류다.
    """
    if outcome not in RESULT_OUTCOMES:
        raise ValueError(f"unknown ledger result outcome: {outcome!r}")
    answered = outcome == RESULT_ANSWERED
    if not answered and (int(output_bytes) != 0 or bool(output_hint)):
        raise ValueError("output fields are only valid for an answered result")
    if (failure_code is None) != (outcome != RESULT_FAILED):
        raise ValueError("failure_code is required exactly when the result failed")
    entry: dict[str, Any] = {
        "timestamp": _timestamp(),
        "phase": PHASE_RESULT,
        "mode": mode,
        "provider": receipt["provider"],
        "sha256_packet_md": str(receipt["sha256_packet_md"]),
        "outcome": outcome,
        "output_bytes": int(output_bytes),
        "output_hint": bool(output_hint),
    }
    if failure_code is not None:
        entry["failure_code"] = int(failure_code)
    return entry


def _timestamp() -> str:
    """UTC 초·마이크로초 시각. 기계 표면이라 언어 설정과 무관하다."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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


# 요약은 접두어만 세지 않는다. 상한을 넘으면 부분 요약이 전체로 읽히므로
# 거절한다. 8MiB 는 줄당 수백 byte 로 수만 줄분이다.
MAX_LEDGER_READ_BYTES = 8 * 1024 * 1024

# 쓰기 쪽 `_timestamp()` 가 만드는 고정 형식. 읽기는 이 모양만 받아 화면에
# 보간한다. 손으로 고른 줄의 timestamp 에 개행·제어문자·bidi 가 섞이면 한 줄
# 토큰 계약과 터미널 상태가 깨진다.
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")

_OUTCOME_KEYS = {
    RESULT_ANSWERED: "answered",
    RESULT_FAILED: "failed",
    RESULT_NOT_OBSERVABLE: "not_observable",
}


def read_ledger_summary() -> dict[str, Any]:
    """대장을 읽어 카운터만 만든다. 경로·질문·본문은 옮기지 않는다.

    대장에는 상대경로가 줄마다 들어 있다. 요약을 읽는 화면은 "얼마나
    나갔는가" 이지 "무엇이 나갔는가" 가 아니므로 집계만 반환한다. 파일이
    없으면 0 이다 — 읽기 표면이 파일을 만들면 켜진 적 없는 대장이 생긴다.
    """
    path = _require_ledger_path()
    return _summarize(_read_private_lines(path))


def _require_ledger_path() -> Path:
    """대장 경로는 env 가 유일한 출처다. 없으면 사용 오류다."""
    path = ledger_path()
    if path is None:
        raise PacketAskError(message("ledger_summary_unset"), codes.USAGE)
    return path


def _read_private_lines(path: Path) -> list[str]:
    """쓰기와 같은 격리 검사로 연다. 남의 파일이나 심링크는 요약하지 않는다."""
    try:
        # O_NOFOLLOW 는 심링크를, O_NONBLOCK 은 리더 없는 FIFO 의 블로킹을 막는다.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PacketAskError(message("ledger_symlink"), codes.CONFINEMENT) from exc
        raise PacketAskError(message("ledger_summary_read"), codes.CONFINEMENT) from exc
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid():
            raise PacketAskError(message("ledger_owner"), codes.CONFINEMENT)
        if not stat.S_ISREG(info.st_mode):
            raise PacketAskError(message("ledger_summary_read"), codes.CONFINEMENT)
        raw = _read_bounded(descriptor)
    finally:
        os.close(descriptor)
    return raw.decode("utf-8", errors="replace").splitlines()


def _read_bounded(descriptor: int) -> bytes:
    """상한까지 읽는다. 넘으면 접두어로 요약하지 않고 거절한다."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_LEDGER_READ_BYTES:
            raise PacketAskError(message("ledger_summary_bytes"), codes.BUDGET)
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_line(line: str) -> dict[str, Any] | None:
    """한 줄을 해석한다. 모르는 형태는 None 이고 호출자가 skipped 로 센다.

    `phase` 가 없으면 egress 다 — 0.11.0 이전 형식이다. 모르는 phase 나 모르는
    outcome 은 이 버전이 해석할 수 없는 줄이므로 세기만 하고 넘어간다. 한 줄
    때문에 요약 전체를 못 읽으면 감사 표면이 못 쓴다.

    읽기는 파일 내용을 **다시 신뢰한다.** 쓰기 쪽이 만든 줄이라도 손으로
    고쳤거나 동기화로 섞였을 수 있으므로, 화면에 그대로 보간되는 timestamp 는
    쓰기 쪽 `_timestamp()` 의 고정 형식과 맞을 때만 받고 카운터에 더할 수치도
    형을 본다. 맞지 않으면 그 줄은 해석 불가로 넘긴다. 억지로 세면 카운터가
    조용히 틀리고, 그대로 보간하면 한 줄 토큰 계약과 터미널이 깨진다.
    """
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    phase = record.get("phase", PHASE_EGRESS)
    if phase not in (PHASE_EGRESS, PHASE_RESULT):
        return None
    if phase == PHASE_RESULT and record.get("outcome") not in _OUTCOME_KEYS:
        return None
    if not _is_optional_timestamp(record.get("timestamp")):
        return None
    size_key = "bytes" if phase == PHASE_EGRESS else "output_bytes"
    if not _is_optional_count(record.get(size_key)):
        return None
    return record


def _is_optional_timestamp(value: Any) -> bool:
    """없거나 쓰기 쪽 고정 형식이어야 한다."""
    if value is None:
        return True
    return isinstance(value, str) and _TIMESTAMP_RE.match(value) is not None


def _is_optional_count(value: Any) -> bool:
    """없거나 bool 이 아닌 음이 아닌 정수여야 한다."""
    if value is None:
        return True
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _summarize(lines: list[str]) -> dict[str, Any]:
    """줄을 세어 고정 키 집계만 만든다."""
    summary: dict[str, Any] = {
        "entries": 0,
        "egress": 0,
        "result": 0,
        "answered": 0,
        "failed": 0,
        "not_observable": 0,
        "unpaired_egress": 0,
        "hint_hits": 0,
        "skipped": 0,
        "packet_bytes_total": 0,
        "output_bytes_total": 0,
        "providers": [],
        "first_timestamp": None,
        "last_timestamp": None,
    }
    providers: set[str] = set()
    egress_by_digest: dict[str, int] = {}
    result_by_digest: dict[str, int] = {}
    timestamps: list[str] = []
    unpaired_without_digest = 0
    for line in lines:
        if not line.strip():
            continue
        summary["entries"] += 1
        record = _parse_line(line)
        if record is None:
            summary["skipped"] += 1
            continue
        unpaired_without_digest += _fold(
            summary, record, providers, egress_by_digest, result_by_digest, timestamps
        )
    summary["providers"] = sorted(providers)
    # 짝은 digest 별로 센다. 같은 패킷의 동시 실행은 순서가 interleaving 할 수
    # 있어 digest + 시각 순서로도 모호해지는데, 그때도 차이는 보수적으로
    # unpaired 에 남는다. digest 가 없는 줄은 **짝짓지 않는다.** 빈 키 한
    # 버킷으로 모으면 digest 없는 egress 와 result 가 서로를 상쇄해 짝이 있는
    # 것처럼 보인다. 짝을 지을 수 없으면 짝이 없다고 말한다.
    summary["unpaired_egress"] = unpaired_without_digest + sum(
        max(0, count - result_by_digest.get(digest, 0))
        for digest, count in egress_by_digest.items()
    )
    if timestamps:
        summary["first_timestamp"] = min(timestamps)
        summary["last_timestamp"] = max(timestamps)
    return summary


def _fold(
    summary: dict[str, Any],
    record: dict[str, Any],
    providers: set[str],
    egress_by_digest: dict[str, int],
    result_by_digest: dict[str, int],
    timestamps: list[str],
) -> int:
    """한 줄을 집계에 접는다. 값은 이름으로 골라 담는다.

    반환값은 "digest 가 없어 짝을 지을 수 없는 egress" 개수다. caller 가
    `unpaired_egress` 에 더한다. timestamp 와 수치는 `_parse_line` 에서 형을
    확인했으므로 여기서 다시 검증하지 않는다.
    """
    phase = record.get("phase", PHASE_EGRESS)
    provider = record.get("provider")
    if isinstance(provider, str):
        providers.add(provider)
    timestamp = record.get("timestamp")
    if isinstance(timestamp, str) and timestamp:
        timestamps.append(timestamp)
    digest = record.get("sha256_packet_md")
    bucket = digest if isinstance(digest, str) and digest else None
    if phase == PHASE_EGRESS:
        summary["egress"] += 1
        summary["packet_bytes_total"] += _count(record.get("bytes"))
        if bucket is None:
            return 1
        egress_by_digest[bucket] = egress_by_digest.get(bucket, 0) + 1
        return 0
    summary["result"] += 1
    summary[_OUTCOME_KEYS[record["outcome"]]] += 1
    summary["output_bytes_total"] += _count(record.get("output_bytes"))
    if record.get("output_hint") is True:
        summary["hint_hits"] += 1
    if bucket is not None:
        result_by_digest[bucket] = result_by_digest.get(bucket, 0) + 1
    return 0


def _count(value: Any) -> int:
    """bool 이 아닌 음이 아닌 정수만 더한다. else 0."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def format_ledger_summary_line(summary: dict[str, Any]) -> str:
    """사람이 읽는 한 줄. 영수증과 같은 append-only 토큰 나열이다.

    provider 목록은 의도적으로 싣지 않는다. 사용자 별명이 공백을 담으면
    토큰 분리가 깨지므로 기계 판독은 JSON 쪽에 둔다.
    """
    return (
        f"packet-ask ledger entries={summary['entries']} egress={summary['egress']}"
        f" result={summary['result']} answered={summary['answered']}"
        f" failed={summary['failed']} not_observable={summary['not_observable']}"
        f" unpaired_egress={summary['unpaired_egress']} hint_hits={summary['hint_hits']}"
        f" skipped={summary['skipped']} packet_bytes={summary['packet_bytes_total']}"
        f" output_bytes={summary['output_bytes_total']}"
        f" first={summary['first_timestamp'] or 'none'}"
        f" last={summary['last_timestamp'] or 'none'}"
    )


def json_ledger_envelope(summary: dict[str, Any]) -> str:
    """stdout 전용 versioned JSON. 본문은 싣지 않는다."""
    body = {"schema": SCHEMA, "ok": True, "ledger": summary}
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"
