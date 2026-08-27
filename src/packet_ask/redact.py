"""시크릿과 신원 패턴을 가리고, 다른 패턴으로 다시 검사한다.

왜 두 번 도나: 첫 정규식이 조용히 놓친 값을 같은 함수의 '성공'으로
취급하면 안 되기 때문이다. 재검증이 실패하면 벤더를 실행하지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SECRET_HEADER_PATTERNS = (
    re.compile(r"(?im)^(\s*(?:Proxy-)?Authorization\s*:\s*)([^\r\n]*)(\r?)$"),
    re.compile(
        r"(?im)^(\s*(?:AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|"
        r"GOOGLE_APPLICATION_CREDENTIALS|AZURE_CLIENT_SECRET)\s*[:=]\s*)"
        r"([^\r\n]*)(\r?)$"
    ),
)
_SECRET_KEY_FRAGMENT = (
    r"[A-Za-z0-9_.-]*(?:api[_-]?key|token|password|private[_-]?key|"
    r"access[_-]?key|client[_-]?secret|secret[_-]?key|secret[_-]?token|"
    r"auth[_-]?token|service[_-]?account[_-]?key)[A-Za-z0-9_.-]*"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    rf"(?im)^(?P<prefix>\s*(?:(?:(?:export|const|let|var|ENV)\s+)?"
    rf"{_SECRET_KEY_FRAGMENT}\s*[:=]|[\'\"]{_SECRET_KEY_FRAGMENT}[\'\"]\s*:)"
    rf"\s*)(?P<rest>[^\r\n]*)(?P<cr>\r?)$"
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"npm_[A-Za-z0-9]{20,}"),
    re.compile(r"(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\bBasic\s+[A-Za-z0-9._~+/=-]+"),
)
_PRIVATE_KEY_RE = re.compile(
    r"(?is)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+82[-\s]?)?0?1[016789][-\s]?\d{3,4}[-\s]?\d{4}(?!\d)"
)
# 재검증용. 스크럽 패턴과 완전히 같으면 놓친 형식을 같이 놓친다.
_VERIFY_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_VERIFY_PHONE_RE = re.compile(r"(?:\+82|0)1[016789]\d{7,8}")
_VERIFY_KEY_RE = re.compile(r"\b(?:sk-|gh[pousr]_|AKIA|github_pat_)[A-Za-z0-9_-]{8,}")


class RedactionError(Exception):
    """스크럽 실패 또는 재검증에서 민감 값이 남은 경우."""


@dataclass
class RedactionReport:
    """무엇을 몇 건 가렸는지. 원문 값은 담지 않는다."""

    private_key_blocks: int = 0
    secret_lines: int = 0
    secret_values: int = 0
    home_paths: int = 0
    emails: int = 0
    phones: int = 0
    extras: dict[str, int] = field(default_factory=dict)


def _redact_secret_rest(rest: str) -> str:
    """대입문 오른쪽만 가리고 따옴표·주석 구조는 남긴다."""
    if not rest:
        return '"[REDACTED]"'
    leading = rest[: len(rest) - len(rest.lstrip())]
    stripped = rest.lstrip()
    if not stripped:
        return leading + '"[REDACTED]"'
    if stripped[0] in {"'", '"'}:
        quote = stripped[0]
        escaped = False
        for idx, ch in enumerate(stripped[1:], 1):
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == quote:
                return f"{leading}{quote}[REDACTED]{quote}{stripped[idx + 1:]}"
        return f"{leading}{quote}[REDACTED]"
    comment = ""
    body = stripped
    comment_match = re.search(r"(\s+#.*)$", body)
    if comment_match:
        comment = comment_match.group(1)
        body = body[: comment_match.start()].rstrip()
    trailing = ""
    while body and body[-1] in ",;}]>)":
        trailing = body[-1] + trailing
        body = body[:-1].rstrip()
    return f'{leading}"[REDACTED]"{trailing}{comment}'


def _assignment_replacer(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{_redact_secret_rest(match.group('rest'))}{match.group('cr')}"


def _home_strings(home: str) -> tuple[str, ...]:
    """macOS /var vs /private/var 별칭을 포함해 홈 경로 변형을 만든다."""
    resolved = str(Path(home).expanduser().resolve())
    variants = {home.rstrip("/"), resolved.rstrip("/")}
    if resolved.startswith("/private"):
        variants.add(resolved[len("/private") :])
    elif resolved.startswith("/var/"):
        variants.add("/private" + resolved)
    return tuple(sorted(variants, key=len, reverse=True))


def scrub_text(text: str, home: str | None = None) -> tuple[str, RedactionReport]:
    """시크릿을 먼저 가리고, 이어서 홈 경로·이메일·전화를 가린다."""
    report = RedactionReport()
    home = home if home is not None else str(Path.home())
    text, n = _PRIVATE_KEY_RE.subn("[REDACTED PRIVATE KEY BLOCK]", text)
    report.private_key_blocks += n
    for pat in _SECRET_HEADER_PATTERNS:
        text, n = pat.subn(lambda m: f"{m.group(1)}[REDACTED]{m.group(3)}", text)
        report.secret_lines += n
    text, n = _SECRET_ASSIGNMENT_RE.subn(_assignment_replacer, text)
    report.secret_lines += n
    for pat in _SECRET_VALUE_PATTERNS:
        text, n = pat.subn("[REDACTED]", text)
        report.secret_values += n
    for variant in _home_strings(home):
        if variant and variant in text:
            count = text.count(variant)
            text = text.replace(variant, "[REDACTED HOME]")
            report.home_paths += count
    text, n = _EMAIL_RE.subn("[REDACTED EMAIL]", text)
    report.emails += n
    text, n = _PHONE_RE.subn("[REDACTED PHONE]", text)
    report.phones += n
    return text, report


def verify_scrubbed(text: str, home: str | None = None) -> None:
    """스크럽과 다른 패턴으로 다시 훑는다. 남으면 RedactionError."""
    home = home if home is not None else str(Path.home())
    leftovers: list[str] = []
    for variant in _home_strings(home):
        if variant and variant in text:
            leftovers.append("home_path")
            break
    if _VERIFY_EMAIL_RE.search(text):
        leftovers.append("email")
    compact = re.sub(r"[\s-]", "", text)
    if _VERIFY_PHONE_RE.search(compact):
        leftovers.append("phone")
    if _VERIFY_KEY_RE.search(text) or "PRIVATE KEY-----" in text:
        leftovers.append("secret")
    if leftovers:
        raise RedactionError("재검증에서 민감 값이 남았습니다: " + ", ".join(leftovers))
