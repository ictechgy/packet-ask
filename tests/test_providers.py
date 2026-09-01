"""프로바이더 카탈로그와 paste 전용 사용자 TOML."""

import os
import re
from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask import providers
from packet_ask.errors import PacketAskError
from packet_ask.providers import (
    BUILTIN_ADAPTERS,
    BUILTIN_IDS,
    BuiltinAdapter,
    ProviderSpec,
    load_catalog,
    lookup_provider,
    resolve_provider_adapter,
    _parse_user_alias,
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


def test_builtin_adapter_registry_is_immutable_and_matches_catalog() -> None:
    """내장 id·mode·adapter 연결은 코드의 immutable mapping 하나와 일치한다."""
    catalog = load_catalog(user_file=None)
    assert frozenset(BUILTIN_ADAPTERS) == BUILTIN_IDS
    for spec in catalog:
        adapter = resolve_provider_adapter(spec)
        assert adapter is BUILTIN_ADAPTERS[spec.provider_id]
        assert spec.adapter_id == spec.provider_id
        assert spec.mode == ("launch" if adapter.launcher_name else "paste")
    with pytest.raises(TypeError):
        BUILTIN_ADAPTERS["evil"] = BuiltinAdapter("launch_glm", "claude")  # type: ignore[index]


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


def test_user_alias_cache_invalidates_when_inode_changes(tmp_path: Path) -> None:
    """같은 path·mtime·size의 교체 overlay를 stale cache로 숨기지 않는다."""
    path = tmp_path / "providers.toml"
    replacement = tmp_path / "replacement.toml"
    first = 'version = 1\n[providers.first]\nlabel = "One"\n'
    second = 'version = 1\n[providers.other]\nlabel = "Two"\n'
    assert len(first.encode("utf-8")) == len(second.encode("utf-8"))
    path.write_text(first, encoding="utf-8")
    original = path.stat()
    providers._USER_ALIAS_CACHE.clear()
    assert "first" in {item.provider_id for item in load_catalog(user_file=path)}
    replacement.write_text(second, encoding="utf-8")
    os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
    replacement.replace(path)
    ids = {item.provider_id for item in load_catalog(user_file=path)}
    assert "other" in ids
    assert "first" not in ids
    assert len(providers._USER_ALIAS_CACHE) == 1


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
    assert gemini.adapter_id is None
    assert resolve_provider_adapter(gemini) is None


@pytest.mark.parametrize(
    "value",
    [
        "bad\nline",
        "bad\x1b]52;c;clipboard\x07",
        "bad\u202ereversed",
        "bad\u2028line",
        "bad\u2029paragraph",
        "x" * 81,
    ],
)
def test_user_alias_rejects_unsafe_label(value: str) -> None:
    """human doctor 출력에 newline·terminal/bidi control·장문을 넣지 못한다."""
    with pytest.raises(PacketAskError) as exc:
        _parse_user_alias("safe", {"label": value})
    assert exc.value.code == codes.CONFINEMENT


def test_user_alias_allows_bounded_unicode_display() -> None:
    """정상 한글 label/note는 paste alias metadata로 유지한다."""
    label = "안전한 별명 " + "می\u200cخواهم"
    note = "가족 " + "👨\u200d👩\u200d👧"
    spec = _parse_user_alias("safe", {"label": label, "notes": note})
    assert spec.label == label
    assert spec.note == note


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


@pytest.mark.parametrize(
    "field",
    ["adapter", "adapter_id", "launcher", "doctor", "registration"],
)
def test_user_toml_cannot_select_builtin_adapter(tmp_path: Path, field: str) -> None:
    """paste alias가 registry 또는 launch hook을 선택할 설정 표면은 없다."""
    path = tmp_path / "providers.toml"
    path.write_text(
        f'version = 1\n[providers.evil]\n{field} = "glm"\n',
        encoding="utf-8",
    )
    with pytest.raises(PacketAskError) as exc:
        load_catalog(user_file=path)
    assert exc.value.code == codes.CONFINEMENT


def test_malformed_user_adapter_spec_fails_closed() -> None:
    """catalog 밖에서 만든 user launch spec도 shared adapter boundary가 거절한다."""
    malformed = ProviderSpec(
        "evil",
        "Evil",
        "user",
        "launch",
        "claude",
        "PACKET_ASK_GLM_KEY",
        "bad",
        "glm",
    )
    with pytest.raises(PacketAskError) as exc:
        resolve_provider_adapter(malformed)
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
