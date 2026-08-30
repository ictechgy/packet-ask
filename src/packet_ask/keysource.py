"""전용 환경변수·macOS Keychain·일회성 prompt에서 프로바이더 키를 고른다."""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.text import message

CREDENTIAL_SOURCES = ("auto", "env", "keychain", "prompt")
CREDENTIAL_PROVIDERS = ("glm", "kimi", "claude")

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


@dataclass(frozen=True)
class CredentialStatus:
    """키 값 없이 저장소 존재와 실제 선택 source만 표현한다."""

    provider: str
    environment: str
    keychain: str
    effective: str


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
    except (ImportError, KeyError):
        return getpass.getuser()


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
        not key
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

    env_value = os.environ.get(env_name, "")
    if source in {"auto", "env"} and env_value.strip():
        return _validate_key(env_value, provider)
    if source == "env":
        raise PacketAskError(message("missing_key", name=env_name), codes.PROVIDER_MISSING)

    if source in {"auto", "keychain"}:
        if source == "keychain" and not _macos_keychain_supported():
            raise PacketAskError(message("keychain_unsupported"), codes.CONFINEMENT)
        key = _read_macos_keychain(provider)
        if key is not None:
            return key
        if source == "keychain":
            raise PacketAskError(
                message("keychain_unavailable", provider=provider),
                codes.PROVIDER_MISSING,
            )

    if source == "prompt":
        return _prompt_provider_key(provider)

    raise PacketAskError(
        message("credential_missing", provider=provider, env=env_name),
        codes.PROVIDER_MISSING,
    )


def credential_status(provider: str) -> CredentialStatus:
    """키 값을 읽지 않고 env/Keychain 존재와 auto 결과를 요약한다."""
    env_name = _provider_env(provider)
    environment = "set" if os.environ.get(env_name, "").strip() else "unset"
    if not _macos_keychain_supported():
        keychain = "unsupported"
    else:
        keychain = "available" if _keychain_item_exists(provider) else "missing"
    if environment == "set":
        effective = "env"
    elif keychain == "available":
        effective = "keychain"
    else:
        effective = "missing"
    return CredentialStatus(provider, environment, keychain, effective)


def store_macos_keychain(provider: str, access: str = "command") -> None:
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
