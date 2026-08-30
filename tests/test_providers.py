"""프로바이더 카탈로그와 paste 전용 사용자 TOML."""

import re
from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.providers import (
    BUILTIN_IDS,
    load_catalog,
    lookup_provider,
)


def test_builtin_catalog_contains_expected_ids() -> None:
    """내장 id 는 paste/glm/kimi/claude/grok/agy 이다."""
    catalog = load_catalog(user_file=None)
    ids = {item.provider_id for item in catalog}
    assert ids == BUILTIN_IDS
    grok = lookup_provider("grok", catalog)
    assert grok.mode == "paste"
    agy = lookup_provider("agy", catalog)
    assert agy.mode == "paste"
    claude = lookup_provider("claude", catalog)
    assert claude.mode == "launch"


def test_user_toml_adds_paste_alias(tmp_path: Path) -> None:
    """사용자 TOML 은 paste 별명만 추가한다."""
    path = tmp_path / "providers.toml"
    path.write_text(
        'version = 1\n[providers.gemini]\nlabel = "Gemini CLI"\n',
        encoding="utf-8",
    )
    catalog = load_catalog(user_file=path)
    gemini = lookup_provider("gemini", catalog)
    assert gemini.source == "user"
    assert gemini.mode == "paste"
    assert gemini.label == "Gemini CLI"


def test_user_toml_rejects_executable(tmp_path: Path) -> None:
    """실행 파일 필드는 거절한다."""
    path = tmp_path / "providers.toml"
    path.write_text(
        'version = 1\n[providers.evil]\nexecutable = "/bin/sh"\n',
        encoding="utf-8",
    )
    with pytest.raises(PacketAskError) as exc:
        load_catalog(user_file=path)
    assert exc.value.code == codes.CONFINEMENT


def test_user_toml_cannot_override_builtin(tmp_path: Path) -> None:
    """내장 id 를 덮어쓰지 못한다."""
    path = tmp_path / "providers.toml"
    path.write_text(
        'version = 1\n[providers.glm]\nlabel = "hijack"\n',
        encoding="utf-8",
    )
    with pytest.raises(PacketAskError) as exc:
        load_catalog(user_file=path)
    assert exc.value.code == codes.CONFINEMENT


def test_unknown_provider_is_usage() -> None:
    """없는 id 는 usage 오류."""
    catalog = load_catalog(user_file=None)
    with pytest.raises(PacketAskError) as exc:
        lookup_provider("nope", catalog)
    assert exc.value.code == codes.USAGE


def test_builtin_catalog_uses_english_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """영어 모드의 내장 카탈로그에는 한글 사용자 문구가 없다."""
    monkeypatch.setenv("PACKET_ASK_LANG", "en")
    for item in load_catalog(user_file=None):
        assert re.search(r"[가-힣]", item.label) is None
        assert re.search(r"[가-힣]", item.note) is None
