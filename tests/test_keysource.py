"""전용 환경변수·macOS Keychain·일회성 prompt credential resolver."""

from __future__ import annotations

import subprocess

import pytest

from packet_ask import codes
from packet_ask.keysource import (
    AUTO_CREDENTIAL_SOURCES,
    CREDENTIAL_BACKENDS,
    CREDENTIAL_SOURCES,
    EnvironmentCredentialBackend,
    credential_status,
    resolve_provider_key,
    store_macos_keychain,
)
from packet_ask.errors import PacketAskError


def test_auto_prefers_dedicated_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto는 전용 환경변수가 있으면 Keychain을 조회하지 않는다."""
    monkeypatch.setenv("PACKET_ASK_GLM_KEY", "env-only-secret")

    def fail_keychain(_provider: str) -> str:
        raise AssertionError("keychain must not be read")

    monkeypatch.setattr("packet_ask.keysource._read_macos_keychain", fail_keychain)
    assert resolve_provider_key("glm", "auto") == "env-only-secret"


def test_credential_backend_registry_is_immutable_and_ordered() -> None:
    """auto 순서와 CLI source 목록은 immutable builtin backend mapping에서 파생한다."""
    assert tuple(CREDENTIAL_BACKENDS) == ("env", "keychain", "prompt")
    assert AUTO_CREDENTIAL_SOURCES == ("env", "keychain")
    assert CREDENTIAL_SOURCES == ("auto", "env", "keychain", "prompt")
    assert CREDENTIAL_BACKENDS["env"].source == "env"
    with pytest.raises(TypeError):
        CREDENTIAL_BACKENDS["evil"] = EnvironmentCredentialBackend()  # type: ignore[index]


def test_auto_uses_packet_ask_keychain_when_environment_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto는 env가 없을 때 packet-ask 소유 Keychain 항목을 사용한다."""
    monkeypatch.delenv("PACKET_ASK_GLM_KEY", raising=False)
    monkeypatch.setattr(
        "packet_ask.keysource._read_macos_keychain",
        lambda _provider: "keychain-only-secret",
    )
    assert resolve_provider_key("glm", "auto") == "keychain-only-secret"


def test_auto_never_falls_through_to_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """auto registry 순서는 prompt를 포함하지 않아 headless 입력을 열지 않는다."""
    monkeypatch.delenv("PACKET_ASK_GLM_KEY", raising=False)
    monkeypatch.setattr("packet_ask.keysource._read_macos_keychain", lambda _provider: None)
    monkeypatch.setattr(
        "packet_ask.keysource._prompt_provider_key",
        lambda _provider: (_ for _ in ()).throw(AssertionError("prompt must not run")),
    )
    with pytest.raises(PacketAskError) as exc:
        resolve_provider_key("glm", "auto")
    assert exc.value.code == codes.PROVIDER_MISSING


def test_keychain_reader_uses_fixed_binary_and_minimal_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """password 조회는 shell 없이 고정 binary와 부모 key 없는 env를 쓴다."""
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    monkeypatch.setattr("packet_ask.keysource._keychain_account", lambda: "local-user")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-secret")
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(args, 0, "keychain-only-secret\n", "")

    monkeypatch.setattr("packet_ask.keysource.subprocess.run", fake_run)
    assert resolve_provider_key("glm", "keychain") == "keychain-only-secret"
    args = captured["args"]
    assert isinstance(args, list)
    assert args[0] == "/usr/bin/security"
    assert args[-1] == "-w"
    assert "packet-ask-glm" in args
    env = captured["env"]
    assert isinstance(env, dict)
    assert "ANTHROPIC_API_KEY" not in env
    assert "parent-secret" not in env.values()


def test_keychain_missing_is_distinct_from_inaccessible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """존재하지 않는 canonical item은 access denied와 다른 고정 문구를 쓴다."""
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    monkeypatch.setattr("packet_ask.keysource._keychain_account", lambda: "local-user")
    monkeypatch.setattr(
        "packet_ask.keysource.subprocess.run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 44, "", ""),
    )
    with pytest.raises(PacketAskError) as exc:
        resolve_provider_key("glm", "keychain")
    assert exc.value.code == codes.PROVIDER_MISSING
    assert "missing" in str(exc.value)


def test_keychain_existing_but_denied_is_inaccessible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """password read 실패 뒤 metadata가 있으면 missing으로 오진하지 않는다."""
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    monkeypatch.setattr("packet_ask.keysource._keychain_account", lambda: "local-user")

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 51 if "-w" in args else 0, "", "")

    monkeypatch.setattr("packet_ask.keysource.subprocess.run", fake_run)
    with pytest.raises(PacketAskError) as exc:
        resolve_provider_key("glm", "keychain")
    assert exc.value.code == codes.PROVIDER_MISSING
    assert "inaccessible" in str(exc.value)


def test_keychain_timeout_has_fixed_non_sensitive_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """timeout은 raw command/error 없이 별도 고정 문구로 보고한다."""
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    monkeypatch.setattr("packet_ask.keysource._keychain_account", lambda: "local-user")
    monkeypatch.setattr(
        "packet_ask.keysource.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["security"], 30)
        ),
    )
    with pytest.raises(PacketAskError) as exc:
        resolve_provider_key("glm", "keychain")
    assert exc.value.code == codes.PROVIDER_MISSING
    assert "timed out" in str(exc.value)
    assert "security" not in str(exc.value)


def test_keychain_spawn_failure_does_not_claim_item_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """security 실행 실패는 item 존재 여부를 추측하지 않는 고정 문구를 쓴다."""
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    monkeypatch.setattr("packet_ask.keysource._keychain_account", lambda: "local-user")
    monkeypatch.setattr(
        "packet_ask.keysource.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("private path")),
    )
    with pytest.raises(PacketAskError) as exc:
        resolve_provider_key("glm", "keychain")
    assert exc.value.code == codes.PROVIDER_MISSING
    assert "could not be read" in str(exc.value)
    assert "private path" not in str(exc.value)


@pytest.mark.parametrize(
    "error",
    [OSError("private path"), subprocess.TimeoutExpired(["security"], 10)],
)
def test_keychain_existence_probe_is_total(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    """metadata probe 자체 실패는 raw 예외 대신 unavailable bool로 끝난다."""
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    monkeypatch.setattr("packet_ask.keysource._keychain_account", lambda: "local-user")
    monkeypatch.setattr(
        "packet_ask.keysource.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    assert credential_status("glm").keychain_item == "missing"


def test_auto_preserves_existing_but_inaccessible_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto도 access denial을 generic missing으로 덮지 않고 provider 전에 멈춘다."""
    monkeypatch.delenv("PACKET_ASK_GLM_KEY", raising=False)
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    monkeypatch.setattr("packet_ask.keysource._keychain_account", lambda: "local-user")

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 51 if "-w" in args else 0, "", "")

    monkeypatch.setattr("packet_ask.keysource.subprocess.run", fake_run)
    with pytest.raises(PacketAskError) as exc:
        resolve_provider_key("glm", "auto")
    assert "inaccessible" in str(exc.value)


def test_keychain_non_utf8_password_is_invalid_without_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """decode 실패는 값·codec 예외를 노출하지 않고 invalid로 분류한다."""
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    monkeypatch.setattr("packet_ask.keysource._keychain_account", lambda: "local-user")
    monkeypatch.setattr(
        "packet_ask.keysource.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
        ),
    )
    with pytest.raises(PacketAskError) as exc:
        resolve_provider_key("glm", "keychain")
    assert exc.value.code == codes.PROVIDER_MISSING
    assert "invalid" in str(exc.value)
    assert "\\xff" not in str(exc.value)


def test_explicit_env_does_not_fallback_to_keychain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """명시 env source는 다른 저장소로 조용히 fallback하지 않는다."""
    monkeypatch.delenv("PACKET_ASK_GLM_KEY", raising=False)
    monkeypatch.setattr(
        "packet_ask.keysource._read_macos_keychain",
        lambda _provider: "must-not-be-used",
    )
    with pytest.raises(PacketAskError) as exc:
        resolve_provider_key("glm", "env")
    assert exc.value.code == codes.PROVIDER_MISSING


def test_explicit_keychain_does_not_fallback_to_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """명시 keychain backend는 설정된 env를 읽지 않는다."""
    monkeypatch.setenv("PACKET_ASK_GLM_KEY", "env-must-not-be-used")
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    monkeypatch.setattr("packet_ask.keysource._read_macos_keychain", lambda _provider: None)
    with pytest.raises(PacketAskError) as exc:
        resolve_provider_key("glm", "keychain")
    assert exc.value.code == codes.PROVIDER_MISSING


def test_prompt_source_uses_no_echo_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """prompt source는 분리된 no-echo reader 결과만 사용한다."""
    monkeypatch.setattr(
        "packet_ask.keysource._prompt_provider_key",
        lambda _provider: "prompt-only-secret",
    )
    assert resolve_provider_key("glm", "prompt") == "prompt-only-secret"


def test_prompt_source_fails_without_interactive_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """prompt source는 비대화형 실행에서 stdin으로 key를 평문 fallback하지 않는다."""
    monkeypatch.setattr("packet_ask.keysource._interactive_terminal", lambda: False)
    with pytest.raises(PacketAskError) as exc:
        resolve_provider_key("glm", "prompt")
    assert exc.value.code == codes.USAGE


def test_status_checks_existence_without_reading_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """status는 Keychain 존재만 확인하고 password retrieval을 하지 않는다."""
    monkeypatch.delenv("PACKET_ASK_GLM_KEY", raising=False)
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    seen: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("packet_ask.keysource.subprocess.run", fake_run)
    status = credential_status("glm")
    assert status.keychain_item == "available"
    assert status.auto_candidate == "keychain"
    assert all("-w" not in args for args in seen)


def test_store_delegates_secret_prompt_to_security_without_key_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keychain 저장은 packet-ask argv에 key를 넣지 않고 security가 직접 묻는다."""
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    monkeypatch.setattr("packet_ask.keysource._interactive_terminal", lambda: True)
    monkeypatch.setattr(
        "packet_ask.keysource._read_macos_keychain",
        lambda _provider: "stored-secret-value",
    )
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("packet_ask.keysource.subprocess.run", fake_run)
    store_macos_keychain("glm", access="command")
    args = captured["args"]
    assert isinstance(args, list)
    assert args[-1] == "-w"
    assert args[args.index("-T") + 1] == "/usr/bin/security"
    assert "prompt-only-secret" not in args
    assert "PACKET_ASK_GLM_KEY" not in " ".join(args)


def test_store_prompt_access_trusts_no_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """prompt access는 Keychain 조회 때마다 승인이 필요하도록 trusted app을 비운다."""
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    monkeypatch.setattr("packet_ask.keysource._interactive_terminal", lambda: True)
    captured: list[str] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.extend(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("packet_ask.keysource.subprocess.run", fake_run)
    store_macos_keychain("glm", access="prompt")
    assert captured[captured.index("-T") + 1] == ""


def test_noninteractive_keychain_store_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """비대화형 set은 key 입력을 기다리지 않고 거절한다."""
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    monkeypatch.setattr("packet_ask.keysource._interactive_terminal", lambda: False)
    with pytest.raises(PacketAskError) as exc:
        store_macos_keychain("glm", access="command")
    assert exc.value.code == codes.USAGE


def test_command_store_requires_successful_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """command ACL 갱신·저장값 검증이 실패하면 성공으로 보고하지 않는다."""
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    monkeypatch.setattr("packet_ask.keysource._interactive_terminal", lambda: True)
    monkeypatch.setattr(
        "packet_ask.keysource.subprocess.run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )
    monkeypatch.setattr(
        "packet_ask.keysource._read_macos_keychain",
        lambda _provider: None,
    )
    with pytest.raises(PacketAskError) as exc:
        store_macos_keychain("glm", access="command")
    assert exc.value.code == codes.INTERNAL


def test_command_store_maps_invalid_readback_to_store_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """저장값 형식 오류도 task-time missing이 아니라 store 검증 실패로 보고한다."""
    monkeypatch.setattr("packet_ask.keysource._macos_keychain_supported", lambda: True)
    monkeypatch.setattr("packet_ask.keysource._interactive_terminal", lambda: True)
    monkeypatch.setattr(
        "packet_ask.keysource.subprocess.run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )

    def invalid_readback(_provider: str) -> str:
        raise PacketAskError("invalid", codes.PROVIDER_MISSING)

    monkeypatch.setattr(
        "packet_ask.keysource._read_macos_keychain",
        invalid_readback,
    )
    with pytest.raises(PacketAskError) as exc:
        store_macos_keychain("glm", access="command")
    assert exc.value.code == codes.INTERNAL
    assert "read-back" in str(exc.value)


def test_short_key_is_rejected_for_output_guard_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """출력 가드 최소 길이보다 짧은 credential은 source 단계에서 거절한다."""
    monkeypatch.setenv("PACKET_ASK_GLM_KEY", "short")
    with pytest.raises(PacketAskError) as exc:
        resolve_provider_key("glm", "env")
    assert exc.value.code == codes.PROVIDER_MISSING
