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


def test_kimi_launch_args_disable_tools(tmp_path: Path) -> None:
    """Kimi 원샷은 quiet/print, 무도구 에이전트, 패킷 work-dir 을 쓴다."""
    from packet_ask.launch import kimi_launch_args

    args = kimi_launch_args(tmp_path, tmp_path / "agent.md", tmp_path / "skills")
    joined = " ".join(args)
    assert "--yolo" not in args
    assert "--auto" not in args
    assert "--add-dir" not in args
    assert "--quiet" in args or "--print" in args
    assert "--agent-file" in args
    assert "--work-dir" in args or "-w" in args
    assert str(tmp_path / "agent.md") in joined


def test_kimi_agent_file_disables_all_tools(tmp_path: Path) -> None:
    """에이전트 파일은 tools: [] 로 도구를 끈다."""
    from packet_ask.launch import write_kimi_no_tools_agent

    path = write_kimi_no_tools_agent(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "tools: []" in text
    assert "subagents: []" in text


def test_kimi_launch_missing_binary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """kimi 바이너리가 없으면 실행하지 않는다."""
    monkeypatch.setattr("packet_ask.launch.shutil.which", lambda _: None)
    dummy = Packet(root=tmp_path, report=RedactionReport())
    (tmp_path / "packet.md").write_text("hello\n", encoding="utf-8")
    with pytest.raises(PacketAskError) as exc:
        launch_kimi(dummy, 1)
    assert exc.value.code == codes.PROVIDER_MISSING
