"""벤더 출력을 불신뢰 봉투로 감싼다."""

from __future__ import annotations

import os
import secrets
import unicodedata

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


def _skip_csi(text: str, index: int) -> int:
    """CSI 시작 다음부터 final byte까지 건너뛴다."""
    while index < len(text):
        if 0x40 <= ord(text[index]) <= 0x7E:
            return index + 1
        index += 1
    return index


def _skip_string_control(text: str, index: int) -> int:
    """OSC/DCS/APC/PM/SOS를 BEL 또는 ST까지 건너뛴다."""
    while index < len(text):
        if text[index] == "\x07":
            return index + 1
        if text[index] == "\x1b" and index + 1 < len(text) and text[index + 1] == "\\":
            return index + 2
        index += 1
    return index


def _strip_terminal_controls(text: str) -> str:
    """터미널 상태를 바꾸는 ANSI와 유니코드 제어문자를 제거한다."""
    cleaned: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\x1b":
            if index + 1 >= len(text):
                break
            introducer = text[index + 1]
            if introducer == "[":
                index = _skip_csi(text, index + 2)
                continue
            if introducer in "]PX^_":
                index = _skip_string_control(text, index + 2)
                continue
            index += 2
            continue
        if char == "\x9b":
            index = _skip_csi(text, index + 1)
            continue
        if char in {"\x90", "\x98", "\x9d", "\x9e", "\x9f"}:
            index = _skip_string_control(text, index + 1)
            continue
        if char in {"\n", "\t"}:
            cleaned.append(char)
        elif char != "\r" and unicodedata.category(char) not in {"Cc", "Cf"}:
            cleaned.append(char)
        index += 1
    return "".join(cleaned)


def sanitize_provider_output(text: str) -> str:
    """원문과 제어문자 제거 결과를 모두 검사한 뒤 안전한 표시 문자열을 돌려준다."""
    guard_provider_output(text)
    cleaned = _strip_terminal_controls(text)
    guard_provider_output(cleaned)
    return cleaned


def wrap_untrusted(text: str) -> str:
    """메인 에이전트가 명령으로 실행하지 않도록 nonce 봉투로 표시한다."""
    text = sanitize_provider_output(text)
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
