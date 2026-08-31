"""런치 전 영수증과 MAIN용 JSON 봉투."""

from __future__ import annotations

import json
from typing import Any

from packet_ask import codes
from packet_ask.packet import Packet
from packet_ask.redact import public_redaction_counts
from packet_ask.scope import ScopedFile

SCHEMA = "packet-ask.v1"

_ERRORS = {
    codes.INTERNAL: ("internal", "The command failed internally."),
    codes.USAGE: ("usage", "Invalid command-line arguments."),
    codes.POLICY: ("policy", "The request was rejected by policy."),
    codes.SCOPE: ("scope", "The selected repository scope was rejected."),
    codes.REDACTION: ("redaction", "The packet failed sensitive-data verification."),
    codes.CONFINEMENT: ("confinement", "A required confinement check failed."),
    codes.BUDGET: ("budget", "The packet exceeded a configured resource limit."),
    codes.PROVIDER_MISSING: ("provider_missing", "The provider or credential is unavailable."),
    codes.PROVIDER_FAILED: ("provider_failed", "The provider failed or timed out."),
    codes.OUTPUT_GUARD: ("output_guard", "The provider output failed a safety check."),
}


def build_receipt(
    provider: str,
    selector: str,
    files: list[ScopedFile],
    diff_text: str | None,
    packet: Packet,
    timeout_seconds: int,
    timeout_source: str,
    timeout_applies: bool,
) -> dict[str, Any]:
    """비밀 값 없이 보낸 범위를 요약한다."""
    paths = _packet_paths(files, diff_text)
    payload = packet.payload_bytes()
    redaction = public_redaction_counts(packet.report)
    return {
        "provider": provider,
        "selector": selector,
        "paths": paths,
        "bytes": len(payload),
        "redaction": redaction,
        "sha256_packet_md": packet.payload_digest(),
        "timeout_seconds": timeout_seconds,
        "timeout_source": timeout_source,
        "timeout_applies": timeout_applies,
    }


def build_packet_summary(
    mode: str,
    selector: str,
    files: list[ScopedFile],
    diff_text: str | None,
    packet: Packet,
    include_breakdown: bool = False,
) -> dict[str, Any]:
    """본문·질문·임시 경로 없이 검증된 packet metadata만 만든다."""
    paths = _packet_paths(files, diff_text)
    summary: dict[str, Any] = {
        "mode": mode,
        "selector": selector,
        "paths": paths,
        "file_count": len(paths),
        "bytes": len(packet.payload_bytes()),
        "redaction": public_redaction_counts(packet.report),
        "sha256_packet_md": packet.payload_digest(),
    }
    if include_breakdown:
        summary["breakdown"] = packet.inspection_breakdown()
    return summary


def _packet_paths(files: list[ScopedFile], diff_text: str | None) -> list[str]:
    """receipt와 inspect가 공유하는 packet 상대경로 목록."""
    paths = [item.relative for item in files]
    if diff_text:
        paths.append("changes.patch")
    return paths


def format_receipt_line(receipt: dict[str, Any]) -> str:
    """사람이 읽는 한 줄 영수증."""
    paths = json.dumps(
        [str(path) for path in receipt["paths"]],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = str(receipt["sha256_packet_md"])[:12]
    timeout = ""
    if "timeout_seconds" in receipt:
        applies = "applies" if receipt.get("timeout_applies") else "informational"
        timeout = (
            f" timeout={receipt['timeout_seconds']}s"
            f"({receipt['timeout_source']},{applies})"
        )
    return (
        f"packet-ask receipt provider={receipt['provider']} "
        f"selector={receipt['selector']} paths={paths} "
        f"bytes={receipt['bytes']} sha256={digest}{timeout}"
    )


def format_packet_summary_line(summary: dict[str, Any]) -> str:
    """터미널 제어문자를 만들지 않는 inspect 단일 행."""
    paths = json.dumps(
        [str(path) for path in summary["paths"]],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    redaction = json.dumps(
        summary["redaction"],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = str(summary["sha256_packet_md"])[:12]
    breakdown = ""
    if "breakdown" in summary:
        breakdown = " breakdown=" + json.dumps(
            summary["breakdown"],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    return (
        f"packet-ask inspect mode={summary['mode']} selector={summary['selector']} "
        f"paths={paths} file_count={summary['file_count']} bytes={summary['bytes']} "
        f"sha256={digest} redaction={redaction}{breakdown}"
    )


def format_timing_line(timing: dict[str, int]) -> str:
    """사람이 읽는 구간 시간. 비밀 값은 넣지 않는다."""
    return (
        f"packet-ask timing preflight_ms={timing['preflight_ms']} "
        f"packet_ms={timing['packet_ms']} launch_ms={timing['launch_ms']} "
        f"total_ms={timing['total_ms']}"
    )


def format_progress_line(elapsed_ms: int) -> str:
    """provider 내용·경로·키가 없는 launch heartbeat."""
    return f"packet-ask progress phase=launch elapsed_ms={max(0, int(elapsed_ms))}"


def _public_timing(timing: dict[str, int]) -> dict[str, int]:
    """허용된 밀리초 키만 복사한다."""
    return {
        "preflight_ms": int(timing["preflight_ms"]),
        "packet_ms": int(timing["packet_ms"]),
        "launch_ms": int(timing["launch_ms"]),
        "total_ms": int(timing["total_ms"]),
    }


def json_envelope(
    receipt: dict[str, Any],
    untrusted_output: str,
    timing: dict[str, int] | None = None,
) -> str:
    """stdout 전용 JSON. 키 값은 넣지 않는다."""
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "ok": True,
        "receipt": receipt,
        "untrusted_output": untrusted_output,
    }
    if timing is not None:
        body["timing"] = _public_timing(timing)
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def json_error_envelope(code: int) -> str:
    """raw argv, 예외 문자열, 경로, key를 포함하지 않는 실패 JSON."""
    kind, message_text = _ERRORS.get(code, _ERRORS[codes.INTERNAL])
    body = {
        "schema": SCHEMA,
        "ok": False,
        "error": {
            "code": int(code),
            "kind": kind,
            "message": message_text,
        },
    }
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def json_summary_envelope(summary: dict[str, Any]) -> str:
    """inspect 성공 metadata만 담는 versioned JSON."""
    body = {
        "schema": SCHEMA,
        "ok": True,
        "summary": summary,
    }
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"
