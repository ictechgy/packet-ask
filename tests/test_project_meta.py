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
    assert ".omc/" in lines
    # `.env*` 는 예외 없이 막는다. 예시 파일은 `env.example` 이라 애초에
    # 이 규칙에 걸리지 않으므로 negation 이 필요 없다. negation 이 있으면
    # 누군가 `.env.example` 을 만들었을 때 그것만 추적돼 버린다.
    assert not any(line.startswith("!.env") for line in lines)
    example = (ROOT / "env.example").read_text(encoding="utf-8")
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
    """git check-ignore 로 .env 계열은 막고 env.example 은 연다."""
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
    for relative in (".env.example", ".env.sample"):
        also_ignored = subprocess.run(
            ["git", "check-ignore", "-q", "--", relative],
            cwd=ROOT,
            check=False,
        )
        assert also_ignored.returncode == 0, relative
    kept = subprocess.run(
        ["git", "check-ignore", "-q", "--", "env.example"],
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


SCOPED_AGENTS_PATHS = (
    "src/packet_ask/AGENTS.md",
    "tests/AGENTS.md",
    "docs/AGENTS.md",
    ".github/AGENTS.md",
)


def test_agents_guidance_exists_at_every_declared_scope() -> None:
    """지침은 AGENTS.md 로 관리하고 서브트리별로 나눈다."""
    assert (ROOT / "AGENTS.md").is_file()
    for relative in SCOPED_AGENTS_PATHS:
        assert (ROOT / relative).is_file(), relative


def test_claude_md_points_at_agents_md_instead_of_duplicating_it() -> None:
    """CLAUDE.md 는 포인터로만 둔다. 지침이 두 곳으로 갈라지면 하나가 낡는다."""
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in text
    for relative in SCOPED_AGENTS_PATHS:
        assert relative in text, relative
    # 규칙 본문이 흘러들면 길어진다. 포인터는 짧게 유지한다.
    assert len(text.splitlines()) < 40


def test_scoped_agents_links_resolve() -> None:
    """인덱스 링크가 옮겨진 파일을 조용히 가리키지 않게 한다."""
    for source in ("AGENTS.md", "CLAUDE.md", *SCOPED_AGENTS_PATHS):
        base = (ROOT / source).parent
        text = (ROOT / source).read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if target.startswith("http"):
                continue
            assert (base / target).resolve().exists(), f"{source} -> {target}"


def test_agents_guidance_is_excluded_from_the_distribution() -> None:
    """에이전트 지침은 기여자 문서이지 런타임 데이터가 아니다.

    `src/packet_ask/AGENTS.md` 는 패키지 디렉터리 안에 있어 기본값으로는
    wheel 과 sdist 에 실렸다. 그러면 지침만 고쳐도 배포물 내용이 바뀌어
    패치 릴리스를 부른다. 실제로 0.6.0 때는 "배포 불필요" 로, 0.7.1 때는
    배포로 갈렸다. 배포물에서 빼서 그 판단이 다시 필요 없게 한다.

    `data/SKILL.md` 는 `install-skills` 가 쓰는 실제 런타임 데이터이므로
    같이 빠지면 안 된다.
    """
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    excluded = data["tool"]["uv"]["build-backend"]["source-exclude"]
    assert "src/packet_ask/AGENTS.md" in excluded
    assert not any("SKILL" in pattern for pattern in excluded)


def test_built_distribution_ships_skill_but_not_agents_guidance() -> None:
    """설정이 아니라 실제 산출물을 본다.

    `source-exclude` 키만 단언하면 백엔드가 그 키를 다르게 해석하거나
    글롭이 빗나가도 통과한다. 빌드된 wheel·sdist 를 직접 연다.
    """
    import tarfile
    import zipfile

    dist = ROOT / "dist"
    version = data_version()
    wheel = dist / f"packet_ask-{version}-py3-none-any.whl"
    sdist = dist / f"packet_ask-{version}.tar.gz"
    if not wheel.is_file() or not sdist.is_file():
        import pytest

        pytest.skip("uv build 산출물이 없다. `uv build` 뒤에 돈다.")

    wheel_names = zipfile.ZipFile(wheel).namelist()
    with tarfile.open(sdist) as archive:
        sdist_names = archive.getnames()

    for names in (wheel_names, sdist_names):
        assert not [name for name in names if name.endswith("AGENTS.md")]
        assert [name for name in names if name.endswith("data/SKILL.md")]


def data_version() -> str:
    """pyproject 의 배포 버전."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def test_the_tool_can_scope_its_own_env_variable_reference() -> None:
    """도구가 자기가 요구하는 문서를 리뷰할 수 있어야 한다.

    `docs/AGENTS.md` 는 환경 변수를 더하면 예시 파일을 고치라고 요구한다.
    그런데 `is_secret_path` 는 `.env` 로 시작하는 모든 이름을 거절하므로
    `.env.example` 을 건드린 diff 는 `review --diff` 가 통째로 거절했다.
    도구가 자기가 시킨 변경을 리뷰하지 못하는 상태였다.

    정책은 완화하지 않는다. 파일명은 내용을 보증하지 않으므로 `.example`
    접미사를 예외로 열면 그 이름에 진짜 키를 적는 순간 통로가 된다. 0.1.7
    에서 `credentials.py` 를 `keysource.py` 로 바꾼 것과 같은 선택으로,
    가드가 아니라 파일 이름을 바꿨다.
    """
    from packet_ask.scope import is_secret_path

    assert (ROOT / "env.example").is_file()
    assert not (ROOT / ".env.example").exists()
    assert is_secret_path(Path("env.example")) is False
    # 가드는 그대로다.
    for still_secret in (".env", ".env.example", ".env.local", "sample.env"):
        assert is_secret_path(Path(still_secret)) is True, still_secret


def test_security_docs_document_the_same_variables_in_both_languages() -> None:
    """영어 문서와 한국어 문서가 갈라지면 계약이 갈라진다.

    실제로 갈라져 있었다. 한국어 쪽에 `PACKET_ASK_LEDGER` 와 `PACKET_ASK_LANG`
    이 없었고, `PACKET_ASK_PROVIDERS_FILE` 은 양쪽 다 없었다. 사람이 표를
    비교해서 잡을 수 있는 종류가 아니다.

    `docs/AGENTS.md` 가 "영어 문서와 한국어 문서를 같이 고친다" 를 규칙으로
    적어 두었으니 그 규칙을 여기서 강제한다.
    """
    pattern = re.compile(r"PACKET_ASK_[A-Z_]+")
    english = set(pattern.findall((ROOT / "SECURITY.md").read_text(encoding="utf-8")))
    korean = set(pattern.findall((ROOT / "SECURITY.ko.md").read_text(encoding="utf-8")))
    assert english == korean, {
        "영어에만": sorted(english - korean),
        "한국어에만": sorted(korean - english),
    }
    # 표가 비어 있으면 위 비교가 공짜로 통과한다.
    assert len(english) >= 9
