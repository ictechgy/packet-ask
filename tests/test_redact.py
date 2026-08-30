"""시크릿·신원 스크럽과 재검증 동작."""

from pathlib import Path

import pytest

from packet_ask.redact import (
    RedactionError,
    public_redaction_counts,
    scrub_text,
    verify_scrubbed,
)


def test_redacts_api_key_assignment() -> None:
    """api_key 대입 값을 가린다."""
    text, report = scrub_text('api_key = "sk-abcdefghijklmnopqrstuvwxyz012345"\n')
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in text
    assert "[REDACTED]" in text
    assert report.secret_lines + report.secret_values >= 1


def test_redaction_module_can_scrub_its_own_detector_source() -> None:
    """정규식 소스의 PRIVATE KEY 문구를 실제 PEM 잔여로 오탐하지 않는다."""
    source = Path(__file__).resolve().parents[1] / "src" / "packet_ask" / "redact.py"
    scrubbed, _ = scrub_text(source.read_text(encoding="utf-8"))
    verify_scrubbed(scrubbed)


def test_public_redaction_counts_are_exact_nonnegative_integers() -> None:
    """receipt/manifest에는 허용된 count만 공개하고 내부 extras를 버린다."""
    _text, report = scrub_text("plain text\n")
    report.extras["internal"] = 1
    counts = public_redaction_counts(report)
    assert set(counts) == {
        "private_key_blocks",
        "secret_lines",
        "secret_values",
        "home_paths",
        "emails",
        "phones",
    }
    assert all(type(value) is int and value >= 0 for value in counts.values())
    assert "extras" not in counts


def test_public_redaction_counts_reject_invalid_field_type() -> None:
    """미래 report 필드 회귀가 원값을 공개 경로에 싣지 못한다."""
    _text, report = scrub_text("plain text\n")
    report.secret_values = "not-a-count"  # type: ignore[assignment]
    with pytest.raises(RedactionError):
        public_redaction_counts(report)


def test_redacts_private_key_block() -> None:
    """PEM 개인키 블록을 가린다."""
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----\n"
    text, report = scrub_text(pem)
    assert "MIIEowIBAAKCAQEA" not in text
    assert report.private_key_blocks == 1


def test_redacts_home_path() -> None:
    """홈 디렉터리 절대경로를 가린다."""
    home = str(Path.home())
    text, report = scrub_text(f"error in {home}/.ssh/config\n")
    assert home not in text
    assert "[REDACTED HOME]" in text
    assert report.home_paths >= 1


def test_redacts_email_and_phone() -> None:
    """이메일과 전화번호를 가린다."""
    text, report = scrub_text("연락처: nina.v@example.com / 010-1234-5678\n")
    assert "nina.v@example.com" not in text
    assert "010-1234-5678" not in text
    assert report.emails >= 1
    assert report.phones >= 1


def test_verify_fails_when_home_path_remains() -> None:
    """재검증은 홈 경로가 남으면 실패한다."""
    home = str(Path.home())
    with pytest.raises(RedactionError):
        verify_scrubbed(f"still here {home}\n")


def test_verify_passes_after_scrub() -> None:
    """스크럽된 텍스트는 재검증을 통과한다."""
    home = str(Path.home())
    text, _ = scrub_text(f"log {home}/proj email a@b.co 010-1111-2222\n")
    verify_scrubbed(text)


def test_verify_does_not_treat_diff_decorator_as_email() -> None:
    """추가된 Python decorator의 +@prefix를 이메일로 오탐하지 않는다."""
    verify_scrubbed("+@pytest.mark.parametrize(\n")


def test_verify_rejects_unredacted_generic_secret_literal() -> None:
    """알려진 토큰 prefix가 없어도 secret-key 문자열 리터럴을 재검증한다."""
    with pytest.raises(RedactionError):
        verify_scrubbed('const cfg = {api_key: "plain-secret-value"}\n')


def test_keeps_identifier_and_type_assignments() -> None:
    """시크릿 이름 식별자·타입 애노테이션은 소스 문법을 깨지 않는다."""
    source = (
        "api_key = os.environ.get('PACKET_ASK_GLM_KEY', '').strip()\n"
        '        "ANTHROPIC_API_KEY": key,\n'
        "private_key_blocks: int = 0\n"
    )
    text, _ = scrub_text(source)
    assert "os.environ.get" in text
    assert ": key," in text
    assert "private_key_blocks: int = 0" in text


def test_redacts_quoted_api_key_literal() -> None:
    """따옴표 리터럴 키 값은 가린다."""
    text, report = scrub_text('api_key = "sk-abcdefghijklmnopqrstuvwxyz012345"\n')
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in text
    assert report.secret_lines + report.secret_values >= 1


def test_redacts_inline_json_api_key() -> None:
    """한 줄 JSON 의 api_key 값도 가린다."""
    text, report = scrub_text('{"ok":1,"api_key":"supersecretvalue123456","x":2}\n')
    assert "supersecretvalue123456" not in text
    assert "[REDACTED]" in text
    assert report.secret_values >= 1


def test_redacts_inline_secret_with_escaped_quote() -> None:
    """이스케이프 따옴표가 있는 문자열도 값 전체를 가린다."""
    source = r'{"api_key":"prefix\"suffix","x":2}' + "\n"
    text, report = scrub_text(source)
    assert "prefix" not in text
    assert "suffix" not in text
    assert r'"api_key":"[REDACTED]"' in text
    assert '"x":2' in text
    assert report.secret_values >= 1


def test_redacts_unquoted_inline_secret_literal_only() -> None:
    """객체 내부의 unquoted 키는 문자열 리터럴만 가리고 표현식은 둔다."""
    source = 'const cfg = {api_key: "plain-secret-value", token: loadToken()}\n'
    text, report = scrub_text(source)
    assert "plain-secret-value" not in text
    assert "loadToken()" in text
    assert report.secret_values >= 1


def test_redacts_url_userinfo() -> None:
    """URL userinfo 비밀번호를 가린다."""
    text, report = scrub_text("postgres://user:supersecretpass@db.example.com/app\n")
    assert "supersecretpass" not in text
    assert "[REDACTED]" in text
    assert report.secret_values >= 1
    verify_scrubbed(text)
