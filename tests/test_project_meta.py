"""공개 메타데이터: 라이선스와 시크릿 ignore."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_license_file_is_mit() -> None:
    """LICENSE 는 MIT 이고 저작권 연도가 있다."""
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in text
    assert "Copyright (c) 2026 Coden" in text
    assert "Permission is hereby granted" in text


def test_pyproject_declares_mit_and_license_file() -> None:
    """패키지 메타데이터에 MIT 와 LICENSE 파일이 선언되어 있다."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license = "MIT"' in text
    assert 'license-files = ["LICENSE"]' in text


def test_gitignore_covers_dotenv_but_keeps_example() -> None:
    """키 파일은 무시하고 예시는 추적한다."""
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in text
    assert "!.env.example" in text
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "PACKET_ASK_GLM_KEY" in example
    assert "sk-" not in example
