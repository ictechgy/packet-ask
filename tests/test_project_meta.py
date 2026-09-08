"""공개 메타데이터: 라이선스와 시크릿 ignore."""

from __future__ import annotations

import ast
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

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


def test_readme_sections_do_not_drift_between_languages() -> None:
    """한쪽 언어에만 절을 더하는 것을 막는다.

    이번 세션에서 두 번 겪었다. SECURITY 변수 표가 갈라져 있었고(#58), 그 직후
    allowlist 절이 영어 README 에만 들어왔다. 문구는 언어마다 다르니 제목을
    비교할 수는 없지만, **개수**는 같아야 한다. 절을 하나 더하면서 대응본을
    빠뜨리면 여기서 걸린다.

    문단 단위 누락까지 잡지는 못한다. 그건 사람이 볼 몫이다.
    """
    english = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    korean = (ROOT / "README.ko.md").read_text(encoding="utf-8").splitlines()
    for level in ("## ", "### "):
        assert sum(1 for line in english if line.startswith(level)) == sum(
            1 for line in korean if line.startswith(level)
        ), level
    assert sum(1 for line in english if line.startswith("```")) == sum(
        1 for line in korean if line.startswith("```")
    )


_ENV_NAME_RE = re.compile(r"PACKET_ASK_[A-Z_]+")


def _source_files() -> list[Path]:
    """패키지 소스 전체. 서브패키지가 생겨도 사각이 남지 않게 재귀로 돈다."""
    return sorted((ROOT / "src" / "packet_ask").rglob("*.py"))


def _literal_env_names() -> set[str]:
    """**문자열 리터럴**에만 나타난 `PACKET_ASK_*` 이름을 모은다.

    정규식으로 파일 전체를 훑으면 주석에 쓴 이름까지 줍는다. 그러면 선언에서
    이름을 빼도 주석이 남는 한 그 변수는 "코드가 읽는 것"으로 남아서, 문서에
    없는 변수를 주석 한 줄로 정당화할 수 있다. 실측하니 `_BIN` 세 이름이
    주석에서만 잡혔다. AST 로 리터럴만 본다.
    """
    names: set[str] = set()
    for source in _source_files():
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                names |= set(_ENV_NAME_RE.findall(node.value))
    return names


def _code_env_names() -> set[str]:
    """코드가 실제로 읽는 `PACKET_ASK_*` 이름 집합을 코드 쪽에서 만든다.

    리터럴 스캔만으로는 `_BIN` 계열을 놓친다. 그 이름은 f-string 으로 만들어져
    소스에 완성된 형태가 없기 때문이다. 선언 상수를 유일한 출처로 합친다.
    """
    from packet_ask import paths

    return _literal_env_names() | set(paths.trusted_executable_override_envs())


def test_security_docs_cover_every_variable_the_code_reads() -> None:
    """양쪽 문서에 **다 같이** 없는 변수를 잡는다.

    영/한 집합 비교는 한쪽만 빠진 경우를 잡는다. `PACKET_ASK_ALLOWLIST_FILE` 이
    그렇게 빠졌었다. 그런데 `PACKET_ASK_GIT_BIN` 은 두 문서와 env.example 어디에도
    없었다 — git 실행 파일을 바꾸는 실재 사용자 표면이고 `test_scope.py` 가
    그 결로를 고정하는데도 말이다. 양쪽 다 없으면 영/한 비교는 공짜로 통과한다.

    그래서 코드에서 만든 집합과 각 문서를 따로 비교한다. 문서에만 있는 이름도
    걸린다. 오타거나 코드가 더 이상 읽지 않는 폐기 변수다.
    """
    expected = _code_env_names()
    # 스캔이 비면 아래 비교가 공짜로 통과한다.
    assert len(expected) >= 13, sorted(expected)
    for name in ("SECURITY.md", "SECURITY.ko.md"):
        found = set(_ENV_NAME_RE.findall((ROOT / name).read_text(encoding="utf-8")))
        assert not expected - found, f"{name} 에 없는 변수: {sorted(expected - found)}"
        assert not found - expected, f"{name} 에만 있는 변수: {sorted(found - expected)}"
    # 예시 파일은 의도적으로 부분 목록이라 등가 비교는 하지 않는다. 대신 코드에
    # 없는 이름을 싣는 것(오타·폐기 변수)만 막는다.
    example = set(_ENV_NAME_RE.findall((ROOT / "env.example").read_text(encoding="utf-8")))
    assert example, "예시 파일에 변수가 없다. 수집이 낡았다."
    assert not example - expected, f"env.example 에만 있는 변수: {sorted(example - expected)}"


def test_trusted_executable_declaration_matches_call_sites_and_registry() -> None:
    """선언이 호출 지점·레지스트리와 **양방향으로** 일치해야 한다.

    `trusted_executable_override_envs()` 는 선언 상수에서만 나오므로 강제 지점이
    없으면 새 실행 파일을 추가한 사람이 상수를 안 고쳐도 아무 일도 안 일어난다.
    반대 방향도 본다. 레지스트리에서 이름을 바꾸면서 선언에 옛 이름을 남기면
    그 이름은 문서와 함께 영구 생존한다.
    """
    from packet_ask import paths
    from packet_ask.providers import builtin_providers

    call_site_re = re.compile(
        r"(?:resolve_trusted_executable|trusted_executable_candidate_exists)\("
        r"\s*[\"']([a-z0-9_-]+)[\"']"
    )
    literals: set[str] = set()
    for source in _source_files():
        literals |= set(call_site_re.findall(source.read_text(encoding="utf-8")))
    binaries = {spec.binary for spec in builtin_providers() if spec.binary}
    # 두 수집기가 비면 아래 포함 관계가 공짜로 통과한다.
    assert literals, "호출 지점 리터럴을 못 찾았다. 수집 패턴이 낡았다."
    assert binaries, "레지스트리에 binary 가 없다. 수집 패턴이 낡았다."
    declared = set(paths.TRUSTED_EXECUTABLES)
    assert not (literals | binaries) - declared, sorted((literals | binaries) - declared)
    assert not declared - (literals | binaries), sorted(declared - (literals | binaries))


def test_paste_only_overrides_really_never_launch() -> None:
    """SECURITY 가 "paste 전용이라 override 가 런치에 영향 없다"고 적은 것을 고정한다.

    그 문장은 선언이 아니라 레지스트리 상태에 달려 있다. grok 을 런치 경로에
    연결하고 선언에만 남겨 두면 모든 테스트가 녹색인 채로 문서가 거짓말이 된다.
    """
    from packet_ask.providers import builtin_providers, resolve_provider_adapter

    paste_only = {
        spec.binary for spec in builtin_providers() if spec.mode == "paste" and spec.binary
    }
    assert {"grok", "agy"} <= paste_only, sorted(paste_only)
    for spec in builtin_providers():
        if spec.binary not in paste_only:
            continue
        adapter = resolve_provider_adapter(spec)
        assert adapter is None or adapter.launcher_name is None, spec.provider_id


def test_repository_declares_its_own_public_surface() -> None:
    """이 저장소가 자기 기능을 쓴다. 추적 파일 전체가 선언 안에 있어야 한다.

    `.packet-ask-surface` 는 유출 방지 allowlist 가 아니라 공개 범위 선언이다.
    이 저장소는 MIT 공개이므로 **추적 파일은 전부 선언 가능**하고, 선언 밖에
    남는 것은 로컬 노트·캐시·스크래치뿐이다. 그래서 "추적 파일 ⊆ 선언" 을
    고정하면 새 최상위 경로를 추가한 사람이 선언을 의도적으로 고치게 된다.
    무엇을 선언해야 하는지 발견할 방법이 없어서 이 기능이 적용되지 않은 채
    남아 있었다.
    """
    from packet_ask.errors import ScopeError
    from packet_ask.surface import SURFACE_FILENAME, assert_within_surface, load_surface

    surface = load_surface(ROOT)
    assert surface is not None, f"{SURFACE_FILENAME} 이 없다. 자기 기능을 안 쓴다."
    assert surface, "선언이 비어 있다."

    # 양성 대조: 같은 함수가 선언 밖 경로를 실제로 거절한다. 이것 없으면 위
    # 포함 관계는 선언이 전부일 때뿐만 아니라 **고장 났을 때**도 통과한다.
    for local_only in (
        "HANDOFF.md",
        ".serena/notes.md",
        ".omc/state.json",
        ".venv/bin/python",
        "dist/packet_ask-0.0.0-py3-none-any.whl",
        "review-diff.patch",
    ):
        with pytest.raises(ScopeError):
            assert_within_surface([local_only], surface)
    # 반대쪽 대조: 선언한 스크래치는 실제로 받아들여진다. 이것 없으면 선언이
    # 아무것도 받아들이지 않아도 위 거절 단언은 전부 통과한다.
    assert_within_surface([".packet-ask-tmp/review-diff.patch"], surface)

    if not (ROOT / ".git").exists():
        pytest.skip("git 저장소가 아니면 추적 파일 목록을 잴 수 없다")
    listing = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    )
    tracked = [line for line in listing.stdout.splitlines() if line]
    assert tracked, "추적 파일을 못 읽었다. 빈 입력이면 포함 관계가 공짜로 통과한다."
    # 술어(_is_declared)를 직접 부르지 않고 CLI 가 쓰는 그 함수로 본다.
    assert_within_surface(tracked, surface)

    # 역방향: 추적 파일을 하나도 덮지 않는 선언 항목은 남은 것이다. 임시로
    # 넓힌 선언이 그대로 남으면 아무 테스트도 실패하지 않는다. 스크래치처럼
    # 의도적으로 미추적 경로를 선언하는 경우가 있으니 화이트리스트를 둔다.
    declared_untracked = {".packet-ask-tmp"}
    stale = [
        entry
        for entry in surface
        if entry not in declared_untracked
        and not any(path == entry or path.startswith(entry + "/") for path in tracked)
    ]
    assert not stale, f"추적 파일을 덮지 않는 선언: {stale}"


def test_gitignore_covers_local_material_the_surface_excludes() -> None:
    """선언이 제외한 로컬 물질을 .gitignore 가 실제로 무시하는지 고정한다.

    선언과 CONTRIBUTING 은 `.packet-ask-tmp` 이 ".gitignore 대상" 이라고 말한다.
    그 상태를 고정하는 테스트가 없으면 gitignore 에서 빠진 채 리뷰 패킷·diff
    조각이 공개 저장소에 커밋돼도 아무 테스트도 실패하지 않는다.
    """
    if not (ROOT / ".git").exists():
        pytest.skip("git 저장소가 아니면 check-ignore 를 쓸 수 없다")
    for relative in (
        ".packet-ask-tmp/review-diff.patch",
        ".venv/bin/python",
        "dist/packet_ask-0.0.0-py3-none-any.whl",
        ".serena/notes.md",
        ".omc/state.json",
        "HANDOFF.md",
    ):
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "--", relative],
            cwd=ROOT,
            check=False,
        )
        assert ignored.returncode == 0, relative


def test_readme_documents_every_guarantee_pair() -> None:
    """README 의 한계 절이 상수와 갈라지는 것을 막는다.

    `output_screen` 을 추가했을 때 SECURITY 는 고치고 README 의 키 열거는
    일곱 개로 남았다. README 의 절·코드블록 **개수**만 비교하는 기존 테스트는
    이것을 못 잡는다. SECURITY 쪽과 같은 사각이다 — 개수 비교는 문단 단위
    누락을 모른다. 키:값 쌍을 하나씩 찾아 presence 로 보고, 사람이 읽는 한 줄의
    토큰 문자열도 그대로 적혀 있는지 본다.
    """
    from packet_ask.receipt import GUARANTEES, _RECEIPT_LINE_GUARANTEES

    for name in ("README.md", "README.ko.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        missing = [
            f"{key}: {value}"
            for key, value in GUARANTEES.items()
            if f"{key}: {value}" not in text
        ]
        assert not missing, f"{name} 에 없는 guarantee: {missing}"
        assert _RECEIPT_LINE_GUARANTEES in text, f"{name} 에 낡은 한 줄 토큰"
