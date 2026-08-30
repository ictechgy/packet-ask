"""프로바이더 카탈로그와 paste 전용 사용자 TOML."""

import os
import re
from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask import providers
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


def test_user_alias_catalog_is_cached_by_file_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """한 task 안에서 동일 overlay TOML을 반복 파싱하지 않는다."""
    path = tmp_path / "providers.toml"
    path.write_text(
        'version = 1\n[providers.gemini]\nlabel = "Gemini CLI"\n',
        encoding="utf-8",
    )
    providers._USER_ALIAS_CACHE.clear()
    reads = 0
    real_read_text = Path.read_text

    def counted(candidate: Path, *args: object, **kwargs: object) -> str:
        nonlocal reads
        if candidate == path:
            reads += 1
        return real_read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    load_catalog(user_file=path)
    load_catalog(user_file=path)
    assert reads == 1


def test_relative_overlay_cache_is_bound_to_resolved_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cwd가 바뀌어도 같은 상대 이름의 다른 overlay를 재사용하지 않는다."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_file = first / "providers.toml"
    second_file = second / "providers.toml"
    first_file.write_text(
        'version = 1\n[providers.first]\nlabel = "One"\n',
        encoding="utf-8",
    )
    second_file.write_text(
        'version = 1\n[providers.other]\nlabel = "Two"\n',
        encoding="utf-8",
    )
    same_mtime = (
        max(first_file.stat().st_mtime_ns, second_file.stat().st_mtime_ns)
        + 1_000_000
    )
    os.utime(first_file, ns=(same_mtime, same_mtime))
    os.utime(second_file, ns=(same_mtime, same_mtime))
    assert first_file.stat().st_size == second_file.stat().st_size
    providers._USER_ALIAS_CACHE.clear()
    monkeypatch.chdir(first)
    first_ids = {item.provider_id for item in load_catalog(Path("providers.toml"))}
    monkeypatch.chdir(second)
    second_ids = {item.provider_id for item in load_catalog(Path("providers.toml"))}
    assert "first" in first_ids
    assert "other" in second_ids
    assert "first" not in second_ids


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
