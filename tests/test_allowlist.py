"""시크릿 이름 추정 면제 allowlist 의 계약.

`is_secret_path` 는 파일명 조각으로 시크릿을 추정한다. 도메인 어휘가 겹치는
저장소에서는 그 추정이 상시 오탐을 낸다. allowlist 는 그 추정 하나만 연다.

여기서 고정하는 성질:

1. 면제는 **추정 규칙만** 연다. 확장자·이름 규칙과 `.env` 접두는 자격증명 파일의
   정의이므로 allowlist 에 적어도 계속 막힌다.
2. 면제는 사람이 명시적으로 적는다. 글롭은 받지 않는다 - 한 줄이 규칙 전체를
   조용히 끄는 것이 이 통제가 막으려는 일이다.
3. 모양이 어긋나면 조용히 무시하지 않고 거절한다. 오타 하나로 면제가 사라지면
   막힌 이유를 찾을 수 없다.
4. 영수증은 이번 패킷에서 실제로 쓰인 면제 수를 숨기지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packet_ask.allowlist import load_allowlist
from packet_ask.errors import ScopeError
from packet_ask.scope import is_secret_path


def write_allowlist(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "allowlist.toml"
    target.write_text(body, encoding="utf-8")
    return target


def test_missing_file_is_an_empty_exemption(tmp_path: Path) -> None:
    assert load_allowlist(tmp_path / "absent.toml") == frozenset()


def test_exempts_only_the_name_segment_heuristic() -> None:
    exempt = frozenset({"src/token_budget.py"})
    assert is_secret_path(Path("src/token_budget.py"), exempt) is False
    assert is_secret_path(Path("src/token_budget.py")) is True


@pytest.mark.parametrize(
    "relative",
    [
        ".env",
        ".env.local",
        "config/.env.production",
        "keys/id_rsa",
        "keys/id_ed25519",
        "credentials.json",
        "deploy/token.pem",
        "deploy/secret.key",
        "npm/.npmrc",
    ],
)
def test_credential_file_definitions_are_never_exempt(relative: str) -> None:
    """확장자·이름 규칙은 추정이 아니라 정의다. 적어도 열리지 않는다."""
    assert is_secret_path(Path(relative), frozenset({relative})) is True


def test_exemption_is_exact_not_prefix_or_sibling() -> None:
    exempt = frozenset({"src/token_budget.py"})
    assert is_secret_path(Path("src/token_budget.py"), exempt) is False
    assert is_secret_path(Path("other/token_budget.py"), exempt) is True
    assert is_secret_path(Path("src/token_budget.py.bak"), exempt) is True


def test_unrelated_names_still_pass_without_any_allowlist() -> None:
    assert is_secret_path(Path("src/tokenizer.py")) is False


def test_globs_are_rejected(tmp_path: Path) -> None:
    """`src/**` 한 줄로 규칙 전체를 끄는 길을 남기지 않는다."""
    target = write_allowlist(
        tmp_path, 'version = 1\nsecret_name_exempt_paths = ["src/*.py"]\n'
    )
    with pytest.raises(ScopeError):
        load_allowlist(target)


@pytest.mark.parametrize("entry", ["/etc/passwd", "../outside/token.py", "a/../../b.py"])
def test_absolute_and_escaping_paths_are_rejected(tmp_path: Path, entry: str) -> None:
    target = write_allowlist(
        tmp_path, f'version = 1\nsecret_name_exempt_paths = ["{entry}"]\n'
    )
    with pytest.raises(ScopeError):
        load_allowlist(target)


def test_wrong_version_is_rejected(tmp_path: Path) -> None:
    target = write_allowlist(tmp_path, 'version = 2\nsecret_name_exempt_paths = []\n')
    with pytest.raises(ScopeError):
        load_allowlist(target)


def test_malformed_toml_is_rejected_not_ignored(tmp_path: Path) -> None:
    """조용히 빈 집합으로 떨어지면 사용자는 면제가 왜 안 먹는지 알 수 없다."""
    target = write_allowlist(tmp_path, "version = 1\nsecret_name_exempt_paths = [\n")
    with pytest.raises(ScopeError):
        load_allowlist(target)


def test_non_string_entries_are_rejected(tmp_path: Path) -> None:
    target = write_allowlist(tmp_path, "version = 1\nsecret_name_exempt_paths = [1]\n")
    with pytest.raises(ScopeError):
        load_allowlist(target)


def test_entry_count_is_bounded(tmp_path: Path) -> None:
    from packet_ask.allowlist import MAX_ALLOWLIST_ENTRIES

    entries = ", ".join(f'"src/f{index}.py"' for index in range(MAX_ALLOWLIST_ENTRIES + 1))
    target = write_allowlist(
        tmp_path, f"version = 1\nsecret_name_exempt_paths = [{entries}]\n"
    )
    with pytest.raises(ScopeError):
        load_allowlist(target)


def test_file_size_is_bounded(tmp_path: Path) -> None:
    from packet_ask.allowlist import MAX_ALLOWLIST_BYTES

    target = write_allowlist(tmp_path, "#" + "x" * (MAX_ALLOWLIST_BYTES + 1))
    with pytest.raises(ScopeError):
        load_allowlist(target)


def test_paths_are_normalized_to_posix(tmp_path: Path) -> None:
    target = write_allowlist(
        tmp_path, 'version = 1\nsecret_name_exempt_paths = ["  src/token_budget.py  "]\n'
    )
    assert load_allowlist(target) == frozenset({"src/token_budget.py"})


def test_receipt_counts_an_exemption_actually_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """이 변경이 여는 가드의 유일한 보완 통제가 영수증 공개다.

    그런데 계수에 대한 테스트가 키 존재 확인뿐이었다. 값이 항상 0 이 되어도
    잡히지 않는다. 보완 통제가 조용히 실패하면 "기본 denylist 보다 넓게
    보냈다" 는 사실 자체가 숨겨진다.
    """
    import json
    import subprocess

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    for command in (
        ["git", "init"],
        ["git", "config", "user.email", "d@e.com"],
        ["git", "config", "user.name", "D"],
    ):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    (repo / "src" / "token_budget.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=repo, check=True, capture_output=True)

    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(
        'version = 1\nsecret_name_exempt_paths = ["src/token_budget.py"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PACKET_ASK_ALLOWLIST_FILE", str(allowlist))
    monkeypatch.chdir(repo)

    from packet_ask.cli import main

    assert main([
        "inspect", "review", "--files", "src/token_budget.py",
        "--json", "--question", "확인",
    ]) == 0
    summary = json.loads(capsys.readouterr().out)["summary"]
    assert summary["secret_name_exempt_used"] == 1


def test_listing_a_path_without_sending_it_counts_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """적어만 두고 보내지 않은 경로까지 세면 영수증이 범위를 부풀린다."""
    import json
    import subprocess

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    for command in (
        ["git", "init"],
        ["git", "config", "user.email", "d@e.com"],
        ["git", "config", "user.name", "D"],
    ):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    (repo / "src" / "token_budget.py").write_text("print(1)\n", encoding="utf-8")
    (repo / "src" / "plain.py").write_text("print(2)\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=repo, check=True, capture_output=True)

    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(
        'version = 1\nsecret_name_exempt_paths = ["src/token_budget.py"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PACKET_ASK_ALLOWLIST_FILE", str(allowlist))
    monkeypatch.chdir(repo)

    from packet_ask.cli import main

    assert main([
        "inspect", "review", "--files", "src/plain.py",
        "--json", "--question", "확인",
    ]) == 0
    summary = json.loads(capsys.readouterr().out)["summary"]
    assert summary["secret_name_exempt_used"] == 0


def test_version_must_be_an_integer_not_a_bool_or_float(tmp_path: Path) -> None:
    """`True == 1` 이라 `version = true` 가 통과했다. 마이그레이션 게이트로서 무결성이 없다."""
    for body in ("version = true\n", "version = 1.0\n", 'version = "1"\n'):
        path = tmp_path / "a.toml"
        path.write_text(body + 'secret_name_exempt_paths = []\n', encoding="utf-8")
        with pytest.raises(ScopeError):
            load_allowlist(path)


def test_entries_that_can_never_take_effect_are_rejected(tmp_path: Path) -> None:
    """면제될 수 없는 경로를 적어 두면 사용자가 면제된 착각을 갖는다.

    발화 시점에 차단되기는 하지만, 로드 때 거절하는 편이 fail-loud 다.
    """
    for entry in (".env", ".env.local", "keys/id_rsa", "a/x.pem", "credentials.json"):
        path = tmp_path / "a.toml"
        path.write_text(
            f'version = 1\nsecret_name_exempt_paths = ["{entry}"]\n', encoding="utf-8"
        )
        with pytest.raises(ScopeError):
            load_allowlist(path)


def test_oversized_allowlist_is_not_read_whole(tmp_path: Path) -> None:
    """환경 변수로 어디든 가리킬 수 있으므로 통째로 읽기 전에 크기를 본다."""
    path = tmp_path / "big.toml"
    path.write_text("#" * (64 * 1024 + 10), encoding="utf-8")
    with pytest.raises(ScopeError):
        load_allowlist(path)


def test_broken_symlink_allowlist_is_rejected_not_treated_as_missing(
    tmp_path: Path,
) -> None:
    """"파일 없음" 과 "링크 깨짐" 은 다르다. 후자는 설정이 있다는 뜻이다."""
    target = tmp_path / "gone.toml"
    link = tmp_path / "allowlist.toml"
    link.symlink_to(target)
    with pytest.raises(ScopeError):
        load_allowlist(link)
