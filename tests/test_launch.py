"""격리 실행기와 Kimi 기본 거절."""

from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.launch import isolated_env, launch_kimi, run_isolated_command
from packet_ask.packet import Packet
from packet_ask.redact import RedactionReport


def test_isolated_env_does_not_copy_parent_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """부모 ANTHROPIC 키를 복사하지 않는다."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-secret")
    env = isolated_env(tmp_path, {"ANTHROPIC_API_KEY": "child-only"})
    assert env["ANTHROPIC_API_KEY"] == "child-only"
    assert "parent-secret" not in env.values()


def test_run_isolated_command_stdin(tmp_path: Path) -> None:
    """stdin 본문이 자식에게 전달된다."""
    script = tmp_path / "echo-stdin"
    script.write_text("#!/bin/sh\ncat\n", encoding="utf-8")
    script.chmod(0o700)
    out = run_isolated_command(
        script,
        [],
        "packet-body\n",
        tmp_path,
        isolated_env(tmp_path, {}),
        timeout=5,
    )
    assert "packet-body" in out


def test_kimi_launch_refused() -> None:
    """v1에서 kimi 자동 실행은 거절한다."""
    dummy = Packet(root=Path("."), report=RedactionReport())
    try:
        launch_kimi(dummy, 1)
        raise AssertionError("should have refused")
    except PacketAskError as exc:
        assert exc.code == codes.CONFINEMENT
