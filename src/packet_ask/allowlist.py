"""시크릿 이름 휴리스틱만 면제하는 사용자 allowlist.

`scope.is_secret_path` 는 파일명 조각으로 시크릿을 추정한다. 그 추정은 도메인 어휘가
겹치는 저장소에서 상시 오탐을 낸다 - `token` 이 컨텍스트 토큰을, `credential` 이 편집
정책을 뜻하는 코드베이스에서는 소스 파일이 통째로 막힌다.

여기서 여는 것은 **추정 규칙 하나뿐**이다. 확장자 규칙(`.pem`, `.key`, `.env`, ...),
이름 규칙(`id_rsa`, `credentials.json`, ...), `.env` 접두, git 메타데이터 경로는 면제
대상이 아니다. 그것들은 추정이 아니라 그 자체로 자격증명 파일의 정의이기 때문이다.

면제는 사람이 한 번 명시적으로 적는다. 글롭을 받지 않는 것도 같은 이유다 - `src/**`
한 줄이 규칙 전체를 조용히 끄는 것이 이 통제가 막으려는 바로 그 일이다.

내용 편집은 그대로 돈다. allowlist 는 "이 경로를 읽어도 된다" 만 말하고 "이 내용을
그대로 보내도 된다" 는 말하지 않는다.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from packet_ask.errors import ScopeError
from packet_ask.text import message

ALLOWLIST_FILE_ENV = "PACKET_ASK_ALLOWLIST_FILE"
ALLOWLIST_VERSION = 1
MAX_ALLOWLIST_BYTES = 64 * 1024
MAX_ALLOWLIST_ENTRIES = 256
MAX_ENTRY_BYTES = 512


def default_user_allowlist_file() -> Path:
    """사용자 allowlist 경로. 테스트는 PACKET_ASK_ALLOWLIST_FILE 로 바꾼다."""
    override = os.environ.get(ALLOWLIST_FILE_ENV, "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "packet-ask" / "allowlist.toml"


def _reject_entry(entry: object) -> str:
    """항목 하나를 검증해 정규화된 상대경로 문자열로 돌려준다."""
    if not isinstance(entry, str):
        raise ScopeError(message("allowlist_entry_type"))
    text = entry.strip()
    if not text or len(text.encode("utf-8")) > MAX_ENTRY_BYTES:
        raise ScopeError(message("allowlist_entry_shape"))
    if "*" in text or "?" in text or "[" in text:
        # 글롭 한 줄이 규칙 전체를 끄는 것이 이 통제가 막으려는 일이다.
        raise ScopeError(message("allowlist_entry_glob", name=text))
    candidate = Path(text)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ScopeError(message("allowlist_entry_relative", name=text))
    # 자격증명 파일 정의는 면제 대상이 아니므로 이 항목은 영원히 발화하지 않는다.
    # 발화 시점에 차단되기는 하지만, 적어 둔 사람은 면제됐다고 믿는다. 로드 때
    # 거절하는 편이 이 통제의 fail-loud 원칙에 맞다.
    from packet_ask.scope import is_inert_exemption

    if is_inert_exemption(candidate):
        raise ScopeError(message("allowlist_entry_inert", name=text))
    return candidate.as_posix()


def load_allowlist(path: Path | None = None) -> frozenset[str]:
    """워크트리 상대경로 allowlist 를 읽는다. 파일이 없으면 빈 집합이다.

    읽기에 실패하거나 모양이 어긋나면 거절한다. 조용히 빈 집합으로 떨어뜨리지
    않는다 - 사용자가 면제를 적어 두었는데 오타 하나로 그것이 무시되면, 막힌
    이유를 영원히 찾지 못한다.
    """
    target = path or default_user_allowlist_file()
    try:
        # 통째로 읽기 전에 크기를 본다. 환경 변수로 어디든 가리킬 수 있다.
        if target.stat().st_size > MAX_ALLOWLIST_BYTES:
            raise ScopeError(message("allowlist_too_large"))
        raw = target.read_bytes()
    except FileNotFoundError:
        # "파일 없음" 과 "링크 깨짐" 은 다르다. 후자는 설정을 두었다는 뜻이므로
        # 조용히 면제를 잃는 대신 멈춘다.
        if target.is_symlink():
            raise ScopeError(message("allowlist_read_failed")) from None
        return frozenset()
    except OSError as exc:
        raise ScopeError(message("allowlist_read_failed")) from exc
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ScopeError(message("allowlist_read_failed")) from exc
    version = parsed.get("version")
    # `True == 1` 이므로 타입을 먼저 본다. 마이그레이션 게이트가 bool 이나
    # float 을 통과시키면 게이트로서 무결성이 없다.
    if not isinstance(version, int) or isinstance(version, bool):
        raise ScopeError(message("allowlist_version"))
    if version != ALLOWLIST_VERSION:
        raise ScopeError(message("allowlist_version"))
    entries = parsed.get("secret_name_exempt_paths", [])
    if not isinstance(entries, list):
        raise ScopeError(message("allowlist_table"))
    if len(entries) > MAX_ALLOWLIST_ENTRIES:
        raise ScopeError(message("allowlist_too_many"))
    return frozenset(_reject_entry(entry) for entry in entries)
