"""서브 프로바이더 카탈로그. 사용자 TOML 은 paste 별명만 받는다."""

from __future__ import annotations

import os
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.text import language, message


@dataclass(frozen=True)
class BuiltinAdapter:
    """코드에 고정된 launch 함수와 doctor 판정 종류."""

    launcher_name: str | None
    doctor_kind: str | None


BUILTIN_ADAPTERS: Mapping[str, BuiltinAdapter] = MappingProxyType(
    {
        "paste": BuiltinAdapter(None, None),
        "glm": BuiltinAdapter("launch_glm", "claude"),
        "kimi": BuiltinAdapter("launch_kimi", "kimi"),
        "claude": BuiltinAdapter("launch_claude", "claude"),
        "grok": BuiltinAdapter(None, None),
        "agy": BuiltinAdapter(None, None),
    }
)
BUILTIN_IDS = frozenset(BUILTIN_ADAPTERS)
_FORBIDDEN_TOML_KEYS = frozenset(
    {
        "executable",
        "argv",
        "env",
        "cwd",
        "command",
        "binary",
        "key",
        "api_key",
        "args",
        "shell",
        "adapter",
        "adapter_id",
        "launcher",
        "doctor",
        "registration",
    }
)
_ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")
_ALIAS_LABEL_MAX_CHARS = 80
_ALIAS_NOTE_MAX_CHARS = 500
_ALIAS_DISPLAY_MAX_BYTES = 4096
_SAFE_ALIAS_FORMAT_CHARS = frozenset({"\u200c", "\u200d"})


@dataclass(frozen=True)
class ProviderSpec:
    """카탈로그 한 줄. 비밀 값은 담지 않는다."""

    provider_id: str
    label: str
    source: str
    mode: str
    binary: str | None
    key_env: str | None
    note: str
    adapter_id: str | None = None


def default_user_providers_file() -> Path:
    """사용자 overlay 경로. 테스트는 PACKET_ASK_PROVIDERS_FILE 로 바꾼다."""
    override = os.environ.get("PACKET_ASK_PROVIDERS_FILE", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "packet-ask" / "providers.toml"


def builtin_providers() -> list[ProviderSpec]:
    """코드로 검토된 내장 목록."""
    return [
        ProviderSpec(
            "paste",
            message("provider_paste_label"),
            "builtin",
            "paste",
            None,
            None,
            message("provider_paste_note"),
            "paste",
        ),
        ProviderSpec(
            "glm",
            "GLM Coding Plan",
            "builtin",
            "launch",
            "claude",
            "PACKET_ASK_GLM_KEY",
            message("provider_glm_note"),
            "glm",
        ),
        ProviderSpec(
            "kimi",
            "Kimi Code",
            "builtin",
            "launch",
            "kimi",
            "PACKET_ASK_KIMI_KEY",
            message("provider_kimi_note"),
            "kimi",
        ),
        ProviderSpec(
            "claude",
            "Claude Code (Anthropic)",
            "builtin",
            "launch",
            "claude",
            "PACKET_ASK_CLAUDE_KEY",
            message("provider_claude_note"),
            "claude",
        ),
        ProviderSpec(
            "grok",
            "Grok Build",
            "builtin",
            "paste",
            "grok",
            None,
            message("provider_grok_note"),
            "grok",
        ),
        ProviderSpec(
            "agy",
            "Antigravity",
            "builtin",
            "paste",
            "agy",
            None,
            message("provider_agy_note"),
            "agy",
        ),
    ]


_UNSET = object()
_USER_ALIAS_CACHE: dict[tuple[str, int, int, str], tuple[ProviderSpec, ...]] = {}


def load_catalog(user_file: Path | None | object = _UNSET) -> list[ProviderSpec]:
    """내장과 사용자 paste 별명을 합친다. None 은 overlay 없음."""
    catalog = list(builtin_providers())
    path = _resolve_user_file(user_file)
    if path is None:
        return catalog
    catalog.extend(_load_user_aliases(path))
    return catalog


def _resolve_user_file(user_file: Path | None | object) -> Path | None:
    """생략하면 기본 경로, None 은 overlay 없음, Path 는 그 파일."""
    if user_file is None:
        return None
    path = default_user_providers_file() if user_file is _UNSET else user_file
    if not isinstance(path, Path) or not path.is_file():
        return None
    return path


def lookup_provider(provider_id: str, catalog: list[ProviderSpec] | None = None) -> ProviderSpec:
    """id 로 스펙을 찾는다. 없으면 usage 오류."""
    items = catalog if catalog is not None else load_catalog()
    for item in items:
        if item.provider_id == provider_id:
            return item
    raise PacketAskError(message("unknown_provider", provider_id=provider_id), codes.USAGE)


def resolve_provider_adapter(spec: ProviderSpec) -> BuiltinAdapter | None:
    """builtin registry 또는 검증된 user paste alias만 반환한다."""
    if spec.source == "user":
        if (
            spec.mode != "paste"
            or spec.binary is not None
            or spec.key_env is not None
            or spec.adapter_id is not None
        ):
            raise PacketAskError(message("provider_adapter_invalid"), codes.CONFINEMENT)
        return None
    if spec.source != "builtin" or spec.adapter_id != spec.provider_id:
        raise PacketAskError(message("provider_adapter_invalid"), codes.CONFINEMENT)
    adapter = BUILTIN_ADAPTERS.get(spec.adapter_id)
    expected_mode = "launch" if adapter and adapter.launcher_name else "paste"
    if adapter is None or spec.mode != expected_mode:
        raise PacketAskError(message("provider_adapter_invalid"), codes.CONFINEMENT)
    if expected_mode == "launch" and adapter.doctor_kind not in {"claude", "kimi"}:
        raise PacketAskError(message("provider_adapter_invalid"), codes.CONFINEMENT)
    if expected_mode == "paste" and adapter.doctor_kind is not None:
        raise PacketAskError(message("provider_adapter_invalid"), codes.CONFINEMENT)
    return adapter


def _load_user_aliases(path: Path) -> list[ProviderSpec]:
    """paste 별명만 읽는다. 실행 필드는 파일 전체를 거절한다."""
    key = _user_alias_cache_key(path)
    if key is not None and key in _USER_ALIAS_CACHE:
        return list(_USER_ALIAS_CACHE[key])
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PacketAskError(message("providers_read_failed"), codes.USAGE) from exc
    if data.get("version") != 1:
        raise PacketAskError(message("providers_version"), codes.USAGE)
    table = data.get("providers")
    if table is None:
        return []
    if not isinstance(table, dict):
        raise PacketAskError(message("providers_table"), codes.USAGE)
    aliases: list[ProviderSpec] = []
    for raw_id, body in table.items():
        aliases.append(_parse_user_alias(raw_id, body))
    if key is not None:
        for stale in [item for item in _USER_ALIAS_CACHE if item[0] == key[0]]:
            _USER_ALIAS_CACHE.pop(stale, None)
        _USER_ALIAS_CACHE[key] = tuple(aliases)
    return aliases


def _user_alias_cache_key(path: Path) -> tuple[str, int, int, str] | None:
    """overlay 내용과 기본 note 언어를 반영하는 프로세스 수명 캐시 키."""
    try:
        info = path.stat()
    except OSError:
        return None
    try:
        canonical = str(path.resolve())
    except OSError:
        return None
    return (canonical, int(info.st_mtime_ns), int(info.st_size), language())


def _parse_user_alias(raw_id: str, body: object) -> ProviderSpec:
    """사용자 항목 하나를 paste 별명으로 검사한다."""
    _assert_safe_id(raw_id)
    if not isinstance(body, dict):
        raise PacketAskError(message("provider_item_table", name=raw_id), codes.USAGE)
    forbidden = _FORBIDDEN_TOML_KEYS.intersection(body)
    if forbidden:
        raise PacketAskError(
            message("provider_alias_forbidden"),
            codes.CONFINEMENT,
        )
    mode = str(body.get("mode", "paste"))
    if mode != "paste":
        raise PacketAskError(message("provider_alias_mode"), codes.CONFINEMENT)
    label = _safe_alias_display(
        body.get("label", raw_id),
        max_chars=_ALIAS_LABEL_MAX_CHARS,
        allow_empty=False,
    )
    note = _safe_alias_display(
        body.get("notes", message("provider_alias_note")),
        max_chars=_ALIAS_NOTE_MAX_CHARS,
        allow_empty=True,
    )
    return ProviderSpec(raw_id, label, "user", "paste", None, None, note)


def _safe_alias_display(value: object, *, max_chars: int, allow_empty: bool) -> str:
    """human doctor 출력에 안전한 bounded label/note만 허용한다."""
    rendered = str(value)
    if (not allow_empty and not rendered.strip()) or len(rendered) > max_chars:
        raise PacketAskError(message("provider_alias_display"), codes.CONFINEMENT)
    if len(rendered.encode("utf-8")) > _ALIAS_DISPLAY_MAX_BYTES:
        raise PacketAskError(message("provider_alias_display"), codes.CONFINEMENT)
    for char in rendered:
        category = unicodedata.category(char)
        if category in {"Cc", "Zl", "Zp"} or (
            category == "Cf" and char not in _SAFE_ALIAS_FORMAT_CHARS
        ):
            raise PacketAskError(message("provider_alias_display"), codes.CONFINEMENT)
    return rendered


def _assert_safe_id(raw_id: str) -> None:
    """내장 덮어쓰기와 이상한 id 를 막는다."""
    if raw_id in BUILTIN_IDS:
        raise PacketAskError(message("provider_builtin_override"), codes.CONFINEMENT)
    if not raw_id or raw_id[0] == "-" or any(ch not in _ID_CHARS for ch in raw_id):
        raise PacketAskError(message("provider_invalid_id", name=raw_id), codes.USAGE)
