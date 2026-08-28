"""서브 프로바이더 카탈로그. 사용자 TOML 은 paste 별명만 받는다."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.text import message

BUILTIN_IDS = frozenset({"paste", "glm", "kimi", "claude", "grok", "agy"})
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
    }
)
_ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


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


def default_user_providers_file() -> Path:
    """사용자 overlay 경로. 테스트는 PACKET_ASK_PROVIDERS_FILE 로 바꾼다."""
    override = os.environ.get("PACKET_ASK_PROVIDERS_FILE", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "packet-ask" / "providers.toml"


def builtin_providers() -> list[ProviderSpec]:
    """코드로 검토된 내장 목록."""
    return [
        ProviderSpec("paste", "패킷만 출력", "builtin", "paste", None, None, "벤더 프로세스 없이 패킷만 출력합니다."),
        ProviderSpec(
            "glm",
            "GLM Coding Plan",
            "builtin",
            "launch",
            "claude",
            "PACKET_ASK_GLM_KEY",
            "신뢰 경로의 claude 바이너리 + Z.ai 자식 환경. doctor는 help 플래그만 확인합니다.",
        ),
        ProviderSpec(
            "kimi",
            "Kimi Code",
            "builtin",
            "launch",
            "kimi",
            "PACKET_ASK_KIMI_KEY",
            "공식 kimi quiet 원샷. 도구는 에이전트 파일로 끕니다.",
        ),
        ProviderSpec(
            "claude",
            "Claude Code (Anthropic)",
            "builtin",
            "launch",
            "claude",
            "PACKET_ASK_CLAUDE_KEY",
            "glm 과 같은 argv. Z.ai 엔드포인트는 넣지 않습니다. doctor는 help 플래그만 확인합니다.",
        ),
        ProviderSpec(
            "grok",
            "Grok Build",
            "builtin",
            "paste",
            "grok",
            None,
            "무도구 원샷 계약이 확인되기 전엔 paste 만 합니다.",
        ),
        ProviderSpec(
            "agy",
            "Antigravity",
            "builtin",
            "paste",
            "agy",
            None,
            "무도구 원샷 계약이 확인되기 전엔 paste 만 합니다.",
        ),
    ]


_UNSET = object()


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


def _load_user_aliases(path: Path) -> list[ProviderSpec]:
    """paste 별명만 읽는다. 실행 필드는 파일 전체를 거절한다."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PacketAskError("providers.toml 을 읽지 못했습니다.", codes.USAGE) from exc
    if data.get("version") != 1:
        raise PacketAskError("providers.toml 의 version 은 1 이어야 합니다.", codes.USAGE)
    table = data.get("providers")
    if table is None:
        return []
    if not isinstance(table, dict):
        raise PacketAskError("providers.toml 의 providers 가 테이블이 아닙니다.", codes.USAGE)
    aliases: list[ProviderSpec] = []
    for raw_id, body in table.items():
        aliases.append(_parse_user_alias(raw_id, body))
    return aliases


def _parse_user_alias(raw_id: str, body: object) -> ProviderSpec:
    """사용자 항목 하나를 paste 별명으로 검사한다."""
    _assert_safe_id(raw_id)
    if not isinstance(body, dict):
        raise PacketAskError(f"{raw_id} 항목이 테이블이 아닙니다.", codes.USAGE)
    forbidden = _FORBIDDEN_TOML_KEYS.intersection(body)
    if forbidden:
        raise PacketAskError(
            "사용자 프로바이더는 paste 만 가능합니다. 실행 파일·인자를 지정할 수 없습니다.",
            codes.CONFINEMENT,
        )
    mode = str(body.get("mode", "paste"))
    if mode != "paste":
        raise PacketAskError("사용자 프로바이더 mode 는 paste 만 허용합니다.", codes.CONFINEMENT)
    label = str(body.get("label", raw_id))
    note = str(body.get("notes", "사용자 paste 별명입니다. 벤더를 실행하지 않습니다."))
    return ProviderSpec(raw_id, label, "user", "paste", None, None, note)


def _assert_safe_id(raw_id: str) -> None:
    """내장 덮어쓰기와 이상한 id 를 막는다."""
    if raw_id in BUILTIN_IDS:
        raise PacketAskError("내장 프로바이더 id 는 덮어쓸 수 없습니다.", codes.CONFINEMENT)
    if not raw_id or raw_id[0] == "-" or any(ch not in _ID_CHARS for ch in raw_id):
        raise PacketAskError(f"잘못된 프로바이더 id 입니다: {raw_id}", codes.USAGE)
