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
