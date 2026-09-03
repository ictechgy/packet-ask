"""런치 전 영수증과 MAIN용 JSON 봉투."""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import Any, Mapping

from packet_ask import codes
from packet_ask.packet import Packet
from packet_ask.redact import public_redaction_counts
from packet_ask.scope import ScopedFile

SCHEMA = "packet-ask.v1"

# 사람은 SECURITY.md 를 읽지만 MAIN 은 영수증·JSON·exit code 를 읽는다.
# 한계가 기계 표면에 없으면 성공 신호가 문서의 부정문보다 크게 읽힌다.
# 산출값이 아니라 코드 상수여야 구현이 변해도 과잉 약속으로 드리프트하지 않는다.
GUARANTEES: Mapping[str, str] = MappingProxyType(
    {
        "leakage": "not-guaranteed",
        "vendor_training": "not-restricted",
        "vendor_local_copy": "uncontrolled",
        "cwd_sandbox": "none",
        "redaction": "denylist",
        "doctor": "help-text-only",
        "policy_gate": "lexical-tripwire",
    }
)
# "leak:no" 는 "유출 없음"으로 정반대로 읽힌다. 오독 방지가 유일한 목적인 줄이므로
# JSON 과 같은 어휘를 그대로 써서 반전 해석이 불가능하게 한다.
_RECEIPT_LINE_GUARANTEES = (
    "leakage:not-guaranteed,cwd_sandbox:none,redaction:denylist"
)

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
    surface: str,
    effort: str | None,
    effort_source: str,
    secret_name_exempt_used: int = 0,
) -> dict[str, Any]:
    """비밀 값 없이 보낸 범위를 요약한다."""
    if (effort is None) != (effort_source == "vendor-default"):
        raise ValueError("effort and effort_source disagree")
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
        "surface": surface,
        # `timeout_seconds` + `timeout_source` 와 같은 짝이다. 값과 출처를 한
        # 필드에 섞으면 enum 도메인에 sentinel 이 들어가고, 나중에 출처가
        # 늘어날 때 값 도메인을 깨야 한다.
        "effort": effort,
        "effort_source": effort_source,
        # 값과 출처가 어긋나면 영수증이 거짓을 말한다. 이 배치의 존재 이유가
        # 출처를 정확히 남기는 것이므로 어긋난 조합은 만들지 않는다.
        #
        # 시크릿 이름 추정을 사용자 allowlist 로 면제한 경로 수. 0 이 아니면 이번
        # 패킷은 기본 denylist 보다 넓은 범위를 보낸 것이고, 영수증은 그 사실을
        # 숨기지 않는다. 자격증명 파일 정의(확장자·이름·.env)는 면제 대상이 아니다.
        "secret_name_exempt_used": secret_name_exempt_used,
        "guarantees": dict(GUARANTEES),
    }


def build_packet_summary(
    mode: str,
    selector: str,
    files: list[ScopedFile],
    diff_text: str | None,
    packet: Packet,
    surface: str,
    include_breakdown: bool = False,
    secret_name_exempt_used: int = 0,
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
        "surface": surface,
        "secret_name_exempt_used": secret_name_exempt_used,
        "guarantees": dict(GUARANTEES),
    }
    if include_breakdown:
        summary["breakdown"] = packet.inspection_breakdown()
    return summary


def build_preview(
    receipt: dict[str, Any],
    mode: str,
    provider_mode: str,
    credential_source: str,
    credential_state: str,
    max_bytes: int,
) -> dict[str, Any]:
    """벤더를 실행하기 전의 런치 계획. 본문과 키 값은 담지 않는다.

    `inspect` 는 의도적으로 provider·credential·timeout 을 만지지 않는다.
    그래서 "실제로 무엇이 어디로 나가려 하는가" 한 장이 비어 있었다. 영수증이
    이미 그 필드를 다 만들므로 재사용하고, 런치 계획에만 있는 것을 더한다.
    """
    preview = dict(receipt)
    preview["mode"] = mode
    preview["provider_mode"] = provider_mode
    preview["credential_source"] = credential_source
    preview["credential_state"] = credential_state
    preview["max_bytes"] = int(max_bytes)
    preview["budget_remaining_bytes"] = max(0, int(max_bytes) - int(receipt["bytes"]))
    # 이 표면의 존재 이유가 "아직 안 나갔다" 이므로 그것을 기계 키로 적는다.
    preview["launch"] = "not-started"
    return preview


def format_preview_line(preview: dict[str, Any]) -> str:
    """사람이 읽는 한 줄 미리보기. 영수증과 같은 append-only 토큰 나열이다."""
    paths = json.dumps(
        [str(path) for path in preview["paths"]],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    applies = "applies" if preview["timeout_applies"] else "informational"
    return (
        f"packet-ask preview provider={preview['provider']} "
        f"mode={preview['mode']} provider_mode={preview['provider_mode']} "
        f"selector={preview['selector']} paths={paths} "
        f"bytes={preview['bytes']} sha256={str(preview['sha256_packet_md'])[:12]} "
        f"budget_remaining={preview['budget_remaining_bytes']} "
        f"timeout={preview['timeout_seconds']}s"
        f"({preview['timeout_source']},{applies}) "
        f"credential={preview['credential_source']}:{preview['credential_state']} "
        f"launch={preview['launch']} "
        f"effort={preview['effort'] or 'none'}({preview['effort_source']}) "
        f"surface={preview['surface']}"
        + (
            f" secret_name_exempt={preview['secret_name_exempt_used']}"
            if preview.get("secret_name_exempt_used")
            else ""
        )
        + f" guarantees={_RECEIPT_LINE_GUARANTEES}"
    )


def json_preview_envelope(preview: dict[str, Any]) -> str:
    """런치하지 않은 계획만 담는 versioned JSON."""
    body = {
        "schema": SCHEMA,
        "ok": True,
        "preview": preview,
    }
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


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
        f" surface={receipt['surface']}"
        f" effort={receipt.get('effort') or 'none'}"
        f"({receipt.get('effort_source', 'vendor-default')})"
        f" guarantees={_RECEIPT_LINE_GUARANTEES}"
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
