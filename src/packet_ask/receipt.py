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
    paths = [item.relative for item in files]
    if diff_text:
        paths.append("changes.patch")
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


def format_timing_line(timing: dict[str, int]) -> str:
    """사람이 읽는 구간 시간. 비밀 값은 넣지 않는다."""
    return (
        f"packet-ask timing preflight_ms={timing['preflight_ms']} "
        f"packet_ms={timing['packet_ms']} launch_ms={timing['launch_ms']} "
        f"total_ms={timing['total_ms']}"
    )


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
