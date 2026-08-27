"""공개 메타데이터: 라이선스와 시크릿 ignore."""

from __future__ import annotations

import subprocess
import tomllib
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
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    classifiers = project.get("classifiers", [])
    assert not any(item.startswith("License ::") for item in classifiers)


def test_gitignore_covers_dotenv_but_keeps_example() -> None:
    """키 파일은 무시하고 예시는 추적한다."""
    lines = [line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert ".env" in lines
    assert ".env.*" in lines
    assert ".envrc" in lines
    assert "!.env.example" in lines
    assert lines.index("!.env.example") > lines.index(".env.*")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "PACKET_ASK_GLM_KEY" in example
    assert "sk-" not in example
    for line in example.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise AssertionError(f"할당이 아닌 예시 줄입니다: {line}")
        _name, _sep, value = stripped.partition("=")
        assert value == "", f"예시에 값이 있습니다: {line}"


def test_gitignore_rejects_env_and_keeps_example() -> None:
    """git check-ignore 로 .env 는 막고 .env.example 은 연다."""
    git_dir = ROOT / ".git"
    if not git_dir.exists():
        return
    for relative in (".env", ".env.local", ".envrc"):
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "--", relative],
            cwd=ROOT,
            check=False,
        )
        assert ignored.returncode == 0, relative
    kept = subprocess.run(
        ["git", "check-ignore", "-q", "--", ".env.example"],
        cwd=ROOT,
        check=False,
    )
    assert kept.returncode == 1
