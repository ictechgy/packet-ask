"""공개 메타데이터: 라이선스와 시크릿 ignore."""

from __future__ import annotations

import re
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


def test_pyproject_declares_github_urls() -> None:
    """PyPI 페이지가 GitHub 저장소를 가리키게 한다."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    urls = data["project"]["urls"]
    assert urls["Homepage"] == "https://github.com/ictechgy/packet-ask"
    assert urls["Repository"] == "https://github.com/ictechgy/packet-ask"
    assert urls["Issues"] == "https://github.com/ictechgy/packet-ask/issues"


def test_gitignore_covers_dotenv_but_keeps_example() -> None:
    """키 파일은 무시하고 예시는 추적한다."""
    lines = [line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert ".env" in lines
    assert ".env.*" in lines
    assert ".envrc" in lines
    assert "HANDOFF.md" in lines
    assert ".serena/" in lines
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
    for relative in (".env", ".env.local", ".envrc", "HANDOFF.md"):
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


def _assert_install_paths(text: str) -> None:
    """설치 안내는 PyPI가 먼저이고 GitHub 직접 설치도 있다."""
    github = "uv tool install git+https://github.com/ictechgy/packet-ask"
    pypi = "uv tool install packet-ask"
    assert github in text
    assert pypi in text
    assert text.index(pypi) < text.index(github)
    assert "pipx install packet-ask" in text
    assert "uv tool upgrade packet-ask" in text
    assert "https://pypi.org/project/packet-ask/" in text
    assert "--unstaged" in text
    assert "kimi-code" in text
    assert "credentials status" in text
    assert "credential-source" in text


def test_readme_is_english_and_shows_install() -> None:
    """기본 README 는 영어이고 PyPI 설치가 기본이다."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    _assert_install_paths(text)
    assert "README.ko.md" in text
    assert not re.search(r"[가-힣]", text)
    assert "does not guarantee" in text.lower() or "does not promise" in text.lower()


def test_korean_readme_exists_and_links_english() -> None:
    """한글 README 는 따로 두고 영어 README 로 연결한다."""
    text = (ROOT / "README.ko.md").read_text(encoding="utf-8")
    _assert_install_paths(text)
    assert "[README.md]" in text or "(README.md)" in text
    assert re.search(r"[가-힣]", text)
    assert "유출이 없음" in text
    assert "지금은 GitHub에서 설치합니다" not in text


def test_security_points_to_github_advisories() -> None:
    """취약점은 GitHub Security Advisories 로 받는다."""
    text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "https://github.com/ictechgy/packet-ask/security/advisories" in text
    assert not re.search(r"[가-힣]", text)
    korean = (ROOT / "SECURITY.ko.md").read_text(encoding="utf-8")
    assert re.search(r"[가-힣]", korean)
    assert "https://github.com/ictechgy/packet-ask/security/advisories" in korean


def test_release_workflow_uses_trusted_publishing() -> None:
    """릴리스는 OIDC 로만 올리고 장기 토큰을 쓰지 않는다."""
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "id-token: write" in workflow
    assert "uv publish" in workflow
    assert "name: pypi" in workflow
    assert "PYPI_API_TOKEN" not in workflow
    assert "TWINE_PASSWORD" not in workflow
    assert "uv build" in workflow
    assert "persist-credentials: false" in workflow
    # 빌드와 업로드를 나눠 업로드 권한이 빌드 잡에 가지 않게 한다.
    assert "needs:" in workflow
    assert "astral-sh/attest-action" in workflow
    assert "workflow_dispatch" not in workflow
    assert "uv version --short" in workflow
    assert workflow.count("id-token: write") == 1


def test_ci_actions_are_pinned_and_declared_pythons_are_tested() -> None:
    """일반 CI도 immutable action과 지원 Python 경계를 검사한다."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    action_lines = [line.strip() for line in workflow.splitlines() if "uses:" in line]
    assert action_lines
    assert all(re.search(r"@[0-9a-f]{40}(?:\s|$)", line) for line in action_lines)
    assert 'python-version: ["3.11", "3.13"]' in workflow


def test_smoke_test_script_exists() -> None:
    """배포 산출물 스모크 테스트가 휠만으로 실행 가능하다."""
    script = ROOT / "tests/smoke.py"
    text = script.read_text(encoding="utf-8")
    assert "packet-ask" in text
    assert "SKILL.md" in text
    assert "pytest" not in text
