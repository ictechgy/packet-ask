"""시크릿·신원 스크럽과 재검증 동작."""

from pathlib import Path

import pytest

from packet_ask.redact import RedactionError, scrub_text, verify_scrubbed


def test_redacts_api_key_assignment() -> None:
    """api_key 대입 값을 가린다."""
    text, report = scrub_text('api_key = "sk-abcdefghijklmnopqrstuvwxyz012345"\n')
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in text
    assert "[REDACTED]" in text
    assert report.secret_lines + report.secret_values >= 1


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


def test_redacts_url_userinfo() -> None:
    """URL userinfo 비밀번호를 가린다."""
    text, report = scrub_text("postgres://user:supersecretpass@db.example.com/app\n")
    assert "supersecretpass" not in text
    assert "[REDACTED]" in text
    assert report.secret_values >= 1
    verify_scrubbed(text)
