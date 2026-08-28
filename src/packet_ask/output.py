"""벤더 출력을 불신뢰 봉투로 감싼다."""

from __future__ import annotations

import os
import secrets

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.text import message

MAX_OUTPUT_BYTES = 1_048_576
_DEDICATED_KEY_ENVS = (
    "PACKET_ASK_GLM_KEY",
    "PACKET_ASK_KIMI_KEY",
    "PACKET_ASK_CLAUDE_KEY",
)
_MIN_KEY_LENGTH = 8

_INJECTION_HINTS = (
    "ignore previous instructions",
    "이전 지시를 무시",
    "you are now",
)


def guard_provider_output(text: str) -> None:
    """전용 키 유출과 과대 출력을 폐기한다. 값은 로그에 남기지 않는다."""
    if len(text.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise PacketAskError(message("output_guard_size"), codes.OUTPUT_GUARD)
    for name in _DEDICATED_KEY_ENVS:
        value = os.environ.get(name, "").strip()
        if len(value) >= _MIN_KEY_LENGTH and value in text:
            raise PacketAskError(message("output_guard_key"), codes.OUTPUT_GUARD)


def wrap_untrusted(text: str) -> str:
    """메인 에이전트가 명령으로 실행하지 않도록 nonce 봉투로 표시한다."""
    nonce = secrets.token_hex(8)
    begin = f"-----BEGIN UNTRUSTED PROVIDER OUTPUT {nonce}-----"
    end = f"-----END UNTRUSTED PROVIDER OUTPUT {nonce}-----"
    body = text.replace("BEGIN UNTRUSTED PROVIDER OUTPUT", "[stripped begin]")
    body = body.replace("END UNTRUSTED PROVIDER OUTPUT", "[stripped end]")
    hints = [hint for hint in _INJECTION_HINTS if hint.lower() in body.lower()]
    header = message("untrusted_header")
    if hints:
        header += " " + message("untrusted_hint")
    return f"{header}\n{begin}\n{body.rstrip()}\n{end}\n"
