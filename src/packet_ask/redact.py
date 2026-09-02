"""시크릿과 신원 패턴을 가리고, 다른 패턴으로 다시 검사한다.

왜 두 번 도나: 첫 정규식이 조용히 놓친 값을 같은 함수의 '성공'으로
취급하면 안 되기 때문이다. 재검증이 실패하면 벤더를 실행하지 않는다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from packet_ask.text import message

_SECRET_HEADER_PATTERNS = (
    re.compile(r"(?im)^(\s*(?:Proxy-)?Authorization\s*:\s*)([^\r\n]*)(\r?)$"),
    re.compile(r"(?im)^(\s*(?:Cookie|Set-Cookie)\s*:\s*)([^\r\n]*)(\r?)$"),
    re.compile(
        r"(?im)^(\s*(?:AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|"
        r"GOOGLE_APPLICATION_CREDENTIALS|AZURE_CLIENT_SECRET)\s*[:=]\s*)"
        r"([^\r\n]*)(\r?)$"
    ),
)
_URL_USERINFO_RE = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)([^/\s@]+)(@)")
_VERIFY_URL_USERINFO_RE = re.compile(r"(?i)://[^/\s:@]+:(?!\[REDACTED\])[^/\s@]+@")
_SECRET_KEY_FRAGMENT = (
    r"[A-Za-z0-9_.-]*(?:api[_-]?key|token|password|private[_-]?key|"
    r"access[_-]?key|client[_-]?secret|secret[_-]?key|secret[_-]?token|"
    r"auth[_-]?token|service[_-]?account[_-]?key)[A-Za-z0-9_.-]*"
)
_INLINE_SECRET_PREFIX_RE = re.compile(
    rf'(?i)(?<![A-Za-z0-9_.-])('
    rf'(?:[\'\"]{_SECRET_KEY_FRAGMENT}[\'\"]|{_SECRET_KEY_FRAGMENT})\s*[:=]\s*'
    rf')([\'\"])'
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
_EMAIL_RE = re.compile(
    r"(?a:\b)[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?a:\b)"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+82[-\s]?)?0?1[016789][-\s]?\d{3,4}[-\s]?\d{4}(?!\d)"
)
_DOTTED_PHONE_RE = re.compile(
    r"(?<![\d.])(?:(?:\+82\.?1[016789])|(?:01[016789]))\.\d{3,4}\.\d{4}(?!\d|\.\d)"
)
# 재검증용. 스크럽 패턴과 완전히 같으면 놓친 형식을 같이 놓친다.
_VERIFY_EMAIL_RE = re.compile(
    r"(?a:\b)[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)
# 검증은 공백·하이픈·괄호를 텍스트 전체에서 지운 뒤 훑는다. 그래서 무관한
# 숫자들이 붙는다. git diff 의 `index <old>..<new> 100644` 가 대표적이다.
# 숫자 경계가 없으면 긴 숫자열 한가운데를 전화번호로 읽는다. 바로 아래
# dotted 후보 패턴은 이미 같은 경계를 갖고 있다.
_VERIFY_PHONE_RE = re.compile(r"(?<!\d)(?:\+82|0)1[016789]\d{7,8}(?!\d)")
_VERIFY_PHONE_DOT_CANDIDATE_RE = re.compile(
    r"(?<![\d.])(?P<number>(?:\+82|0)1[016789][\d.]{7,10})(?!\d|\.\d)"
)
_VERIFY_KEY_RE = re.compile(r"\b(?:sk-|gh[pousr]_|AKIA|github_pat_)[A-Za-z0-9_-]{8,}")
_VERIFY_PRIVATE_KEY_HEADER_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
)
_VERIFY_SECRET_LITERAL_RE = re.compile(
    rf'(?i)(?<![A-Za-z0-9_.-])(?:[\'\"]{_SECRET_KEY_FRAGMENT}[\'\"]|'
    rf'{_SECRET_KEY_FRAGMENT})\s*[:=]\s*(?P<quote>[\'\"])(?!\[REDACTED\](?P=quote))'
)
_EMAIL_LOCAL_EXTRA = frozenset("._%+-")
_EMAIL_DOMAIN_EXTRA = frozenset(".-")
_EMAIL_DOT_EQUIVALENTS = str.maketrans({"\u3002": ".", "\uff0e": ".", "\uff61": "."})
_LIKELY_ASCII_TLDS = frozenset(
    {
        "aero",
        "app",
        "asia",
        "biz",
        "cloud",
        "com",
        "coop",
        "dev",
        "edu",
        "gov",
        "info",
        "int",
        "jobs",
        "mil",
        "mobi",
        "museum",
        "name",
        "net",
        "online",
        "org",
        "pro",
        "shop",
        "site",
        "store",
        "tech",
        "tel",
        "travel",
        "xyz",
    }
)
_SHADOW_IGNORABLE_CODEPOINTS = frozenset({0x034F, 0x115F, 0x1160, 0x2800, 0x3164})


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


_PUBLIC_REDACTION_FIELDS = (
    "private_key_blocks",
    "secret_lines",
    "secret_values",
    "home_paths",
    "emails",
    "phones",
)


def public_redaction_counts(report: RedactionReport) -> dict[str, int]:
    """receipt/manifest에 허용된 비민감 count만 복사한다."""
    counts: dict[str, int] = {}
    for name in _PUBLIC_REDACTION_FIELDS:
        value = getattr(report, name)
        if type(value) is not int or value < 0:
            raise RedactionError(message("redaction_report_invalid"))
        counts[name] = value
    return counts


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


def _looks_like_secret_literal(rest: str) -> bool:
    """식별자·타입 애노테이션은 남기고 리터럴만 가린다."""
    stripped = rest.strip().rstrip(",;")
    if not stripped:
        return True
    if stripped[0] in {"'", '"'}:
        return True
    if re.match(r"^(?:int|str|bool|bytes|float|None|list|dict|tuple|Path|Optional)\b", stripped):
        return False
    if re.match(r"^[A-Za-z_][A-Za-z0-9_\.]*(\(|$)", stripped):
        return bool(re.fullmatch(r"[A-Za-z0-9_\-]{16,}", stripped))
    return True


def _assignment_replacer(match: re.Match[str]) -> str:
    """리터럴 우변만 가린다. 소스 식별자를 깨지 않기 위해서다."""
    rest = match.group("rest")
    if not _looks_like_secret_literal(rest):
        return match.group(0)
    return f"{match.group('prefix')}{_redact_secret_rest(rest)}{match.group('cr')}"


def _redact_inline_secret_literals(text: str) -> tuple[str, int]:
    """JSON/객체의 인라인 문자열 값을 escape를 인식해 끝까지 가린다."""
    rendered: list[str] = []
    cursor = 0
    count = 0
    while match := _INLINE_SECRET_PREFIX_RE.search(text, cursor):
        quote = match.group(2)
        value_end = match.end()
        escaped = False
        while value_end < len(text):
            char = text[value_end]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                break
            value_end += 1
        rendered.append(text[cursor : match.end()])
        rendered.append("[REDACTED]")
        if value_end < len(text):
            rendered.append(quote)
            cursor = value_end + 1
        else:
            cursor = value_end
        count += 1
    rendered.append(text[cursor:])
    return "".join(rendered), count


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
    text, n = _URL_USERINFO_RE.subn(r"\1[REDACTED]\3", text)
    report.secret_values += n
    text, n = _redact_inline_secret_literals(text)
    report.secret_values += n
    for variant in _home_strings(home):
        if variant and variant in text:
            count = text.count(variant)
            text = text.replace(variant, "[REDACTED HOME]")
            report.home_paths += count
    text, n = _EMAIL_RE.subn("[REDACTED EMAIL]", text)
    report.emails += n
    text, n = _DOTTED_PHONE_RE.subn("[REDACTED PHONE]", text)
    report.phones += n
    text, n = _PHONE_RE.subn("[REDACTED PHONE]", text)
    report.phones += n
    return text, report


def _detection_shadow(text: str) -> str:
    """원문을 바꾸지 않고 compatibility 문자와 format 난독화만 검출용으로 푼다."""
    normalized = unicodedata.normalize("NFKC", text).translate(_EMAIL_DOT_EQUIVALENTS)
    canonical: list[str] = []
    for char in normalized:
        category = unicodedata.category(char)
        codepoint = ord(char)
        if (
            category == "Cf"
            or codepoint in _SHADOW_IGNORABLE_CODEPOINTS
            or 0xFE00 <= codepoint <= 0xFE0F
            or 0xE0100 <= codepoint <= 0xE01EF
        ):
            continue
        if category == "Pd":
            canonical.append("-")
            continue
        if category == "Nd":
            canonical.append(str(unicodedata.decimal(char)))
            continue
        canonical.append(char)
    return unicodedata.normalize("NFKC", "".join(canonical))


def _is_unicode_atom(char: str) -> bool:
    """Unicode mailbox local/domain label에 허용할 letter·mark·number."""
    return unicodedata.category(char)[:1] in {"L", "M", "N"}


def _is_email_local_char(char: str) -> bool:
    return _is_unicode_atom(char) or char in _EMAIL_LOCAL_EXTRA


def _is_email_domain_char(char: str) -> bool:
    return _is_unicode_atom(char) or char in _EMAIL_DOMAIN_EXTRA


def _valid_email_local(local: str) -> bool:
    """decorator의 기호-only local을 제외한 conservative mailbox local."""
    if not local or local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    return any(unicodedata.category(char)[:1] in {"L", "N"} for char in local)


def _valid_email_domain(domain: str, local: str) -> bool:
    """Unicode label 두 개 이상과 두 글자 이상의 letter/mark TLD를 요구한다."""
    labels = domain.rstrip(".").split(".")
    if len(labels) < 2 or any(not label for label in labels):
        return False
    for label in labels:
        if label.startswith("-") or label.endswith("-"):
            return False
        if not all(_is_unicode_atom(char) or char == "-" for char in label):
            return False
        if not any(unicodedata.category(char)[:1] in {"L", "N"} for char in label):
            return False
    tld = labels[-1]
    lowered_tld = tld.lower()
    if lowered_tld.startswith("xn--"):
        suffix = lowered_tld[4:]
        return bool(suffix) and suffix[-1] != "-" and all(
            char.isascii() and (char.isalnum() or char == "-") for char in suffix
        )
    if len(tld) < 2 or not all(
        unicodedata.category(char)[:1] in {"L", "M"} for char in tld
    ):
        return False
    mailbox_has_unicode = any(ord(char) > 127 for char in local + domain)
    tld_is_ascii = all(char.isascii() for char in tld)
    if mailbox_has_unicode and tld_is_ascii:
        return len(tld) == 2 or lowered_tld in _LIKELY_ASCII_TLDS
    return True


def _contains_unicode_email_candidate(text: str) -> bool:
    """NFKC/Cf shadow에서 ASCII·international mailbox 후보를 찾는다."""
    for index, char in enumerate(text):
        if char != "@":
            continue
        start = index
        while start > 0 and _is_email_local_char(text[start - 1]):
            start -= 1
        end = index + 1
        while end < len(text) and _is_email_domain_char(text[end]):
            end += 1
        local = text[start:index]
        domain = text[index + 1 : end]
        if _valid_email_local(local) and _valid_email_domain(domain, local):
            return True
    return False


def _contains_mixed_dotted_phone(compact: str) -> bool:
    """dash/space 제거 뒤 dot이 하나 이상 남은 Korean phone candidate만 본다."""
    for match in _VERIFY_PHONE_DOT_CANDIDATE_RE.finditer(compact):
        candidate = match.group("number")
        if "." not in candidate:
            continue
        normalized = candidate.replace(".", "")
        if re.fullmatch(r"(?:\+82|0)1[016789]\d{7,8}", normalized):
            return True
    return False


def verify_scrubbed(text: str, home: str | None = None) -> None:
    """스크럽과 다른 패턴으로 다시 훑는다. 남으면 RedactionError."""
    home = home if home is not None else str(Path.home())
    leftovers: list[str] = []
    for variant in _home_strings(home):
        if variant and variant in text:
            leftovers.append("home_path")
            break
    shadow = _detection_shadow(text)
    if _VERIFY_EMAIL_RE.search(shadow) or _contains_unicode_email_candidate(shadow):
        leftovers.append("email")
    compact = re.sub(r"[\s\-()]", "", shadow)
    if _VERIFY_PHONE_RE.search(compact) or _contains_mixed_dotted_phone(compact):
        leftovers.append("phone")
    secret_family_left = any(pattern.search(shadow) for pattern in _SECRET_VALUE_PATTERNS)
    if (
        secret_family_left
        or _VERIFY_KEY_RE.search(shadow)
        or _VERIFY_PRIVATE_KEY_HEADER_RE.search(shadow)
    ):
        leftovers.append("secret")
    if _VERIFY_SECRET_LITERAL_RE.search(shadow):
        leftovers.append("secret_literal")
    if _VERIFY_URL_USERINFO_RE.search(shadow):
        leftovers.append("secret")
    if leftovers:
        raise RedactionError(message("redaction_leftovers", kinds=", ".join(leftovers)))
