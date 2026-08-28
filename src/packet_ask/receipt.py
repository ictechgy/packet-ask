"""런치 전 영수증과 MAIN용 JSON 봉투."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from packet_ask.packet import Packet
from packet_ask.scope import ScopedFile

SCHEMA = "packet-ask.v1"


def build_receipt(
    provider: str,
    selector: str,
    files: list[ScopedFile],
    diff_text: str | None,
    packet: Packet,
) -> dict[str, Any]:
    """비밀 값 없이 보낸 범위를 요약한다."""
    paths = [item.relative for item in files]
    if diff_text:
        paths.append("changes.patch")
    payload = (packet.root / "packet.md").read_bytes()
    redaction = {
        key: value
        for key, value in packet.report.__dict__.items()
        if key != "extras"
    }
    return {
        "provider": provider,
        "selector": selector,
        "paths": paths,
        "bytes": len(payload),
        "redaction": redaction,
        "sha256_packet_md": hashlib.sha256(payload).hexdigest(),
    }


def format_receipt_line(receipt: dict[str, Any]) -> str:
    """사람이 읽는 한 줄 영수증."""
    paths = ",".join(str(path) for path in receipt["paths"])
    digest = str(receipt["sha256_packet_md"])[:12]
    return (
        f"packet-ask receipt provider={receipt['provider']} "
        f"selector={receipt['selector']} paths={paths} "
        f"bytes={receipt['bytes']} sha256={digest}"
    )


def json_envelope(receipt: dict[str, Any], untrusted_output: str) -> str:
    """stdout 전용 JSON. 키 값은 넣지 않는다."""
    body = {
        "schema": SCHEMA,
        "ok": True,
        "receipt": receipt,
        "untrusted_output": untrusted_output,
    }
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"
