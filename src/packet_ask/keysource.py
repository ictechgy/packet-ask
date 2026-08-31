"""전용 환경변수·macOS Keychain·일회성 prompt에서 프로바이더 키를 고른다."""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.text import message

CREDENTIAL_PROVIDERS = ("glm", "kimi", "claude")
MIN_CREDENTIAL_LENGTH = 8

_PROVIDER_ENV = {
    "glm": "PACKET_ASK_GLM_KEY",
    "kimi": "PACKET_ASK_KIMI_KEY",
    "claude": "PACKET_ASK_CLAUDE_KEY",
}
_KEYCHAIN_SERVICE = {
    "glm": "packet-ask-glm",
    "kimi": "packet-ask-kimi",
    "claude": "packet-ask-claude",
}
_SECURITY_BIN = Path("/usr/bin/security")
_KEYCHAIN_TIMEOUT_SECONDS = 30
_KEYCHAIN_STORE_TIMEOUT_SECONDS = 60
_MAX_KEY_CHARS = 4096


class CredentialBackend(Protocol):
    """코드에 고정된 credential source의 최소 read/status 계약."""

    source: str

    def resolve_optional(self, provider: str) -> str | None: ...

    def available(self, provider: str) -> bool: ...


@dataclass(frozen=True)
class EnvironmentCredentialBackend:
    source: str = "env"

    def resolve_optional(self, provider: str) -> str | None:
        value = os.environ.get(_provider_env(provider), "")
        return _validate_key(value, provider) if value.strip() else None

    def available(self, provider: str) -> bool:
        return bool(os.environ.get(_provider_env(provider), "").strip())


@dataclass(frozen=True)
class MacOSKeychainCredentialBackend:
    source: str = "keychain"

    def resolve_optional(self, provider: str) -> str | None:
        return _read_macos_keychain(provider)

    def available(self, provider: str) -> bool:
        return _keychain_item_exists(provider)


@dataclass(frozen=True)
class PromptCredentialBackend:
    source: str = "prompt"

    def resolve_optional(self, provider: str) -> str | None:
        return _prompt_provider_key(provider)

    def available(self, provider: str) -> bool:
        _provider_env(provider)
        return _interactive_terminal()


CREDENTIAL_BACKENDS: Mapping[str, CredentialBackend] = MappingProxyType(
    {
        "env": EnvironmentCredentialBackend(),
        "keychain": MacOSKeychainCredentialBackend(),
        "prompt": PromptCredentialBackend(),
    }
)
AUTO_CREDENTIAL_SOURCES = ("env", "keychain")
CREDENTIAL_SOURCES = ("auto", *CREDENTIAL_BACKENDS)


@dataclass(frozen=True)
class CredentialStatus:
    """키 값 없이 저장소 존재와 실제 선택 source만 표현한다."""

    provider: str
    environment: str
    keychain_item: str
    auto_candidate: str


def _provider_env(provider: str) -> str:
    """실행형 내장의 전용 환경변수 이름."""
    try:
        return _PROVIDER_ENV[provider]
    except KeyError as exc:
        raise PacketAskError(
            message("credential_provider", provider=provider),
            codes.USAGE,
        ) from exc


def _keychain_service(provider: str) -> str:
    """packet-ask가 소유하는 canonical Keychain service 이름."""
    _provider_env(provider)
    return _KEYCHAIN_SERVICE[provider]


def _keychain_account() -> str:
    """환경변수 대신 현재 uid의 macOS 계정 이름을 사용한다."""
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError, OSError):
        try:
            return getpass.getuser()
        except (KeyError, OSError) as exc:
            raise PacketAskError(message("credential_account"), codes.INTERNAL) from exc


def _security_env() -> dict[str, str]:
    """부모 클라우드 키를 복사하지 않는 `/usr/bin/security` 환경."""
    return {
        "HOME": str(Path.home()),
        "PATH": "/usr/bin:/bin",
        "LANG": os.environ.get("LANG", "C"),
        "LC_ALL": os.environ.get("LC_ALL", "C"),
    }


def _macos_keychain_supported() -> bool:
    """고정된 macOS security 바이너리가 있을 때만 Keychain을 연다."""
    return sys.platform == "darwin" and _SECURITY_BIN.is_file()


def _validate_key(value: str, provider: str) -> str:
    """빈 값·제어문자·비정상적으로 큰 값을 자식 환경에 넣지 않는다."""
    key = value.strip()
    if (
        len(key) < MIN_CREDENTIAL_LENGTH
        or len(key) > _MAX_KEY_CHARS
        or any(char in key for char in ("\x00", "\r", "\n"))
    ):
        raise PacketAskError(
            message("credential_invalid", provider=provider),
            codes.PROVIDER_MISSING,
        )
    return key


def _read_macos_keychain(provider: str) -> str | None:
    """canonical 항목의 password만 캡처하고 오류·메타데이터는 버린다."""
    if not _macos_keychain_supported():
        return None
    command = [
        str(_SECURITY_BIN),
        "find-generic-password",
        "-a",
        _keychain_account(),
        "-s",
        _keychain_service(provider),
        "-w",
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=_security_env(),
            timeout=_KEYCHAIN_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None
    if result.returncode != 0:
        return None
    return _validate_key(result.stdout, provider)


def _keychain_item_exists(provider: str) -> bool:
    """status용. password를 요청하지 않고 항목 존재만 본다."""
    if not _macos_keychain_supported():
        return False
    command = [
        str(_SECURITY_BIN),
        "find-generic-password",
        "-a",
        _keychain_account(),
        "-s",
        _keychain_service(provider),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_security_env(),
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _interactive_terminal() -> bool:
    """secret prompt를 echo 없이 받을 수 있는 전경 터미널인지 본다."""
    return sys.stdin.isatty() and sys.stderr.isatty()


def _prompt_provider_key(provider: str) -> str:
    """저장하지 않는 일회성 키를 getpass로 읽는다."""
    if not _interactive_terminal():
        raise PacketAskError(message("credential_prompt_tty"), codes.USAGE)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            value = getpass.getpass(message("credential_prompt", provider=provider))
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning) as exc:
        raise PacketAskError(message("credential_prompt_failed"), codes.USAGE) from exc
    return _validate_key(value, provider)


def resolve_provider_key(provider: str, source: str = "auto") -> str:
    """명시된 source만 사용하며 auto도 env→canonical Keychain에서 멈춘다."""
    env_name = _provider_env(provider)
    if source not in CREDENTIAL_SOURCES:
        raise PacketAskError(message("credential_source", source=source), codes.USAGE)

    if source == "auto":
        for backend_name in AUTO_CREDENTIAL_SOURCES:
            value = CREDENTIAL_BACKENDS[backend_name].resolve_optional(provider)
            if value is not None:
                return value
        raise PacketAskError(
            message("credential_missing", provider=provider, env=env_name),
            codes.PROVIDER_MISSING,
        )

    if source == "keychain" and not _macos_keychain_supported():
        raise PacketAskError(message("keychain_unsupported"), codes.CONFINEMENT)
    value = CREDENTIAL_BACKENDS[source].resolve_optional(provider)
    if value is not None:
        return value
    if source == "env":
        raise PacketAskError(message("missing_key", name=env_name), codes.PROVIDER_MISSING)
    if source == "keychain":
        raise PacketAskError(
            message("keychain_unavailable", provider=provider),
            codes.PROVIDER_MISSING,
        )
    raise PacketAskError(message("credential_prompt_failed"), codes.USAGE)


def credential_status(provider: str) -> CredentialStatus:
    """키 값을 읽지 않고 env/Keychain 존재와 auto 결과를 요약한다."""
    environment = "set" if CREDENTIAL_BACKENDS["env"].available(provider) else "unset"
    if not _macos_keychain_supported():
        keychain_item = "unsupported"
    else:
        keychain_item = (
            "available" if CREDENTIAL_BACKENDS["keychain"].available(provider) else "missing"
        )
    if environment == "set":
        auto_candidate = "env"
    elif keychain_item == "available":
        auto_candidate = "keychain"
    else:
        auto_candidate = "missing"
    return CredentialStatus(provider, environment, keychain_item, auto_candidate)


def store_macos_keychain(provider: str, access: str) -> None:
    """secret 입력은 `/usr/bin/security -w`가 직접 담당하게 한다."""
    _provider_env(provider)
    if access not in {"command", "prompt"}:
        raise PacketAskError(message("credential_access", access=access), codes.USAGE)
    if not _macos_keychain_supported():
        raise PacketAskError(message("keychain_unsupported"), codes.CONFINEMENT)
    if not _interactive_terminal():
        raise PacketAskError(message("credential_store_tty"), codes.USAGE)
    trusted_app = str(_SECURITY_BIN) if access == "command" else ""
    command = [
        str(_SECURITY_BIN),
        "add-generic-password",
        "-U",
        "-a",
        _keychain_account(),
        "-s",
        _keychain_service(provider),
        "-T",
        trusted_app,
        "-w",
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            env=_security_env(),
            timeout=_KEYCHAIN_STORE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PacketAskError(message("credential_store_failed"), codes.INTERNAL) from exc
    if result.returncode != 0:
        raise PacketAskError(message("credential_store_failed"), codes.INTERNAL)
    if access == "command":
        try:
            verified_key = _read_macos_keychain(provider)
        except PacketAskError as exc:
            raise PacketAskError(
                message("credential_store_verify"),
                codes.INTERNAL,
            ) from exc
        if verified_key is None:
            raise PacketAskError(message("credential_store_verify"), codes.INTERNAL)
