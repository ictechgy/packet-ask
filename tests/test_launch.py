"""격리 실행기와 Kimi 기본 거절."""

import os
import time
from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.launch import (
    _cleanup_kimi_sessions,
    ensure_kimi_config,
    isolated_env,
    launch_kimi,
    run_isolated_command,
)
from packet_ask.packet import Packet
from packet_ask.redact import RedactionReport


def test_isolated_env_does_not_copy_parent_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """부모 ANTHROPIC 키와 BASE_URL 을 복사하지 않는다."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    env = isolated_env(tmp_path, {"ANTHROPIC_API_KEY": "child-only"})
    assert env["ANTHROPIC_API_KEY"] == "child-only"
    assert "ANTHROPIC_BASE_URL" not in env
    assert "parent-secret" not in env.values()
    assert "https://evil.example" not in env.values()
    assert env["HOME"] == str(tmp_path)


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


def test_run_isolated_command_drains_output_while_writing_stdin(tmp_path: Path) -> None:
    """자식이 stdout을 먼저 채워도 stdin 전달과 timeout이 교착되지 않는다."""
    import sys

    script = tmp_path / "output-before-stdin"
    script.write_text(
        "#!" + sys.executable + "\n"
        "import sys\n"
        "sys.stdout.write('o' * 131072)\n"
        "sys.stdout.flush()\n"
        "data = sys.stdin.read()\n"
        "sys.stdout.write('\\nstdin=' + str(len(data)))\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    body = "i" * 262144
    out = run_isolated_command(
        script,
        [],
        body,
        tmp_path,
        isolated_env(tmp_path, {}),
        timeout=5,
    )
    assert out.endswith(f"stdin={len(body)}")


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
    monkeypatch.setattr("packet_ask.launch.resolve_trusted_executable", lambda _: None)
    dummy = Packet(root=tmp_path, report=RedactionReport())
    (tmp_path / "packet.md").write_text("hello\n", encoding="utf-8")
    with pytest.raises(PacketAskError) as exc:
        launch_kimi(dummy, 1)
    assert exc.value.code in {codes.PROVIDER_MISSING, codes.CONFINEMENT}


def _pid_is_alive(pid: int) -> bool:
    """프로세스가 아직 있는지 본다. 시그널은 보내지 않는다."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_kimi_without_dedicated_key_does_not_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PACKET_ASK_KIMI_KEY 없으면 벤더를 띄우지 않는다."""
    monkeypatch.delenv("PACKET_ASK_KIMI_KEY", raising=False)
    monkeypatch.setattr("packet_ask.launch.require_launchable", lambda _name: None)
    monkeypatch.setattr(
        "packet_ask.launch.resolve_trusted_executable",
        lambda name: Path("/usr/bin/true") if name == "kimi" else None,
    )
    monkeypatch.setattr("packet_ask.launch.provider_home", lambda _name: tmp_path)
    called: list[bool] = []

    def fake_run(executable, argv, stdin_text, cwd, env, timeout):  # noqa: ANN001
        called.append(True)
        return "ok"

    monkeypatch.setattr("packet_ask.launch.run_isolated_command", fake_run)
    dummy = Packet(root=tmp_path, report=RedactionReport())
    (tmp_path / "packet.md").write_text("hello\n", encoding="utf-8")
    with pytest.raises(PacketAskError) as exc:
        launch_kimi(dummy, 1)
    assert exc.value.code == codes.PROVIDER_MISSING
    assert called == []


def test_timeout_kills_process_group(tmp_path: Path) -> None:
    """timeout 시 자식의 손자까지 프로세스 그룹으로 죽인다."""
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "spawn.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'sleep 30 &\n'
        f'echo $! > "{pid_file}"\n'
        "wait\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    with pytest.raises(PacketAskError) as exc:
        run_isolated_command(
            script,
            [],
            "",
            tmp_path,
            isolated_env(tmp_path, {}),
            timeout=1,
        )
    assert exc.value.code == codes.PROVIDER_FAILED
    deadline = time.time() + 2
    while time.time() < deadline and not pid_file.is_file():
        time.sleep(0.05)
    assert pid_file.is_file(), "자식 pid를 기록하기 전에 종료되면 안 된다"
    child_pid = int(pid_file.read_text(encoding="utf-8").strip())
    time.sleep(0.3)
    assert _pid_is_alive(child_pid) is False


def test_interrupt_kills_provider_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl+C가 새 세션의 provider 자식을 고아로 남기지 않는다."""
    import subprocess

    from packet_ask import launch

    script = tmp_path / "provider.sh"
    script.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    script.chmod(0o700)
    holder: dict[str, subprocess.Popen[str]] = {}
    real_spawn = launch._spawn_isolated

    def spy(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        proc = real_spawn(*args, **kwargs)  # type: ignore[arg-type]
        holder["proc"] = proc
        return proc

    def interrupt(*_args: object, **_kwargs: object) -> tuple[list[object], list[object], list[object]]:
        raise KeyboardInterrupt

    monkeypatch.setattr("packet_ask.launch._spawn_isolated", spy)
    monkeypatch.setattr("packet_ask.launch.select.select", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_isolated_command(
            script,
            [],
            "",
            tmp_path,
            isolated_env(tmp_path, {}),
            timeout=5,
        )
    assert holder["proc"].poll() is not None


def test_timeout_kills_sigterm_ignoring_descendant(tmp_path: Path) -> None:
    """세션 리더가 SIGTERM 에 죽어도 SIGTERM 을 무시한 손자는 SIGKILL 로 끝낸다."""
    import sys

    pid_file = tmp_path / "ignore.pid"
    script = tmp_path / "ignore-term.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"{sys.executable} -c "
        f"\"import os, pathlib, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path(r'{pid_file}').write_text(str(os.getpid())); time.sleep(60)\" &\n"
        "wait\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    with pytest.raises(PacketAskError) as exc:
        run_isolated_command(
            script,
            [],
            "",
            tmp_path,
            isolated_env(tmp_path, {}),
            timeout=1,
        )
    assert exc.value.code == codes.PROVIDER_FAILED
    deadline = time.time() + 2
    while time.time() < deadline and not pid_file.is_file():
        time.sleep(0.05)
    assert pid_file.is_file()
    child_pid = int(pid_file.read_text(encoding="utf-8").strip())
    deadline = time.time() + 2
    while time.time() < deadline and _pid_is_alive(child_pid):
        time.sleep(0.05)
    assert _pid_is_alive(child_pid) is False


def test_timeout_kills_descendant_after_leader_exits(tmp_path: Path) -> None:
    """리더가 먼저 끝나도 spawn 때 저장한 그룹에 SIGKILL 을 보낸다."""
    import sys

    pid_file = tmp_path / "orphan.pid"
    script = tmp_path / "leader-exit.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"{sys.executable} -c "
        f"\"import os, pathlib, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path(r'{pid_file}').write_text(str(os.getpid())); time.sleep(60)\" &\n"
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    with pytest.raises(PacketAskError) as exc:
        run_isolated_command(
            script,
            [],
            "",
            tmp_path,
            isolated_env(tmp_path, {}),
            timeout=1,
        )
    assert exc.value.code in {codes.PROVIDER_FAILED, codes.OUTPUT_GUARD}
    deadline = time.time() + 2
    while time.time() < deadline and not pid_file.is_file():
        time.sleep(0.05)
    assert pid_file.is_file()
    child_pid = int(pid_file.read_text(encoding="utf-8").strip())
    deadline = time.time() + 2
    while time.time() < deadline and _pid_is_alive(child_pid):
        time.sleep(0.05)
    assert _pid_is_alive(child_pid) is False


def test_run_isolated_command_caps_stderr(tmp_path: Path) -> None:
    """stderr 가 한도를 넘으면 출력을 버린다."""
    import sys

    from packet_ask.output import MAX_OUTPUT_BYTES

    script = tmp_path / "flood.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"{sys.executable} -c \"import sys; sys.stderr.write('a' * {MAX_OUTPUT_BYTES + 4096})\"\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    with pytest.raises(PacketAskError) as exc:
        run_isolated_command(
            script,
            [],
            "",
            tmp_path,
            isolated_env(tmp_path, {}),
            timeout=5,
        )
    assert exc.value.code == codes.OUTPUT_GUARD


def test_kimi_config_does_not_write_api_key(tmp_path: Path) -> None:
    """격리 config.toml 에 API 키를 쓰지 않는다."""
    ensure_kimi_config(tmp_path)
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "KIMI_API_KEY" not in text
    assert "api_key" not in text.lower()


def test_kimi_config_rejects_symlink_file(tmp_path: Path) -> None:
    """격리 설정 파일 심링크를 따라가 다른 파일을 덮지 않는다."""
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    kimi_home = tmp_path / "kimi-home"
    kimi_home.mkdir()
    (kimi_home / "config.toml").symlink_to(victim)
    with pytest.raises(PacketAskError) as exc:
        ensure_kimi_config(kimi_home)
    assert exc.value.code == codes.CONFINEMENT
    assert victim.read_text(encoding="utf-8") == "keep"


def test_kimi_cleanup_failure_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """세션 정리가 실패하면 성공으로 숨기지 않는다."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()

    def fail_cleanup(*_args: object, **_kwargs: object) -> None:
        raise OSError("blocked")

    monkeypatch.setattr("packet_ask.launch.shutil.rmtree", fail_cleanup)
    with pytest.raises(PacketAskError) as exc:
        _cleanup_kimi_sessions(tmp_path)
    assert exc.value.code == codes.INTERNAL


def test_kimi_config_disables_tools_without_star_allowlist(tmp_path: Path) -> None:
    """도구 차단은 매칭되지 않는 명시적 allowlist 로 한다. '*' 관용구는 오해를 부른다."""
    from packet_ask.launch import ensure_kimi_config

    ensure_kimi_config(tmp_path)
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert 'enabled = ["*"]' not in text
    assert "packet-ask-no-such-tool" in text


def test_glm_print_flag_does_not_eat_tools() -> None:
    """-p 가 --tools 를 프롬프트로 삼키지 않게 빈 프롬프트를 둔다."""
    from packet_ask.launch import glm_argv

    args = glm_argv()
    assert "--tools" in args
    assert args[args.index("--tools") + 1] == ""
    if "-p" in args:
        assert args[args.index("-p") + 1] != "--tools"
    else:
        assert "--print" in args


def test_glm_passes_key_in_child_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GLM 키와 Z.ai 엔드포인트는 자식 환경에만 들어간다."""
    from packet_ask.launch import launch_glm

    monkeypatch.setenv("PACKET_ASK_GLM_KEY", "glm-child-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    monkeypatch.setattr("packet_ask.launch.require_launchable", lambda _name: None)
    monkeypatch.setattr(
        "packet_ask.launch.resolve_trusted_executable",
        lambda name: Path("/usr/bin/true") if name == "claude" else None,
    )
    monkeypatch.setattr("packet_ask.launch.provider_home", lambda _name: tmp_path)
    captured: dict[str, object] = {}

    def fake_run(executable, argv, stdin_text, cwd, env, timeout):  # noqa: ANN001
        captured["argv"] = argv
        captured["env"] = env
        return "ok"

    monkeypatch.setattr("packet_ask.launch.run_isolated_command", fake_run)
    dummy = Packet(root=tmp_path, report=RedactionReport())
    (tmp_path / "packet.md").write_text("hello\n", encoding="utf-8")
    assert launch_glm(dummy, 1) == "ok"
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["ANTHROPIC_API_KEY"] == "glm-child-key"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "glm-child-key"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.z.ai/api/anthropic"
    assert "parent-secret" not in env.values()
    assert "https://evil.example" not in env.values()
    assert env["HOME"] == str(tmp_path)
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "--bare" in argv
    assert "--tools" in argv
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert env["DISABLE_ERROR_REPORTING"] == "1"
    assert env["DISABLE_TELEMETRY"] == "1"


def test_glm_uses_explicit_keychain_source_and_guards_reflection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keychain 키를 자식에 넣되 벤더가 반사하면 출력을 폐기한다."""
    from packet_ask.launch import launch_glm

    monkeypatch.setattr("packet_ask.launch.require_launchable", lambda _name: None)
    monkeypatch.setattr(
        "packet_ask.launch.resolve_trusted_executable",
        lambda name: Path("/usr/bin/true") if name == "claude" else None,
    )
    monkeypatch.setattr("packet_ask.launch.provider_home", lambda _name: tmp_path)
    seen: list[tuple[str, str]] = []

    def fake_resolve(provider: str, source: str) -> str:
        seen.append((provider, source))
        return "keychain-secret-value"

    monkeypatch.setattr("packet_ask.launch.resolve_provider_key", fake_resolve)
    monkeypatch.setattr(
        "packet_ask.launch.run_isolated_command",
        lambda *_args, **_kwargs: "keychain-secret\x1b[31m-value",
    )
    dummy = Packet(root=tmp_path, report=RedactionReport())
    (tmp_path / "packet.md").write_text("hello\n", encoding="utf-8")
    with pytest.raises(PacketAskError) as exc:
        launch_glm(dummy, 1, credential_source="keychain")
    assert exc.value.code == codes.OUTPUT_GUARD
    assert seen == [("glm", "keychain")]


def test_require_launchable_probes_only_selected_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """런치는 고른 프로바이더 바이너리만 --help 한다."""
    from packet_ask import doctor
    from packet_ask.launch import require_launchable

    binary = tmp_path / "kimi"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    probed: list[str] = []

    def fake_help(executable: str) -> str | None:
        probed.append(executable)
        if executable == "kimi":
            return "--quiet\n--agent-file\n--work-dir\n--skills-dir\n"
        return None

    monkeypatch.setattr("packet_ask.doctor.resolve_trusted_executable", lambda name: binary if name == "kimi" else None)
    monkeypatch.setattr("packet_ask.doctor._help_text", fake_help)
    require_launchable("kimi")
    assert probed == ["kimi"]


def test_claude_sub_uses_dedicated_key_without_z_ai(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """claude 서브는 PACKET_ASK_CLAUDE_KEY 만 쓰고 Z.ai URL 을 넣지 않는다."""
    from packet_ask.launch import launch_claude

    monkeypatch.setenv("PACKET_ASK_CLAUDE_KEY", "anthropic-child-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    monkeypatch.setattr("packet_ask.launch.require_launchable", lambda _name: None)
    monkeypatch.setattr(
        "packet_ask.launch.resolve_trusted_executable",
        lambda name: Path("/usr/bin/true") if name == "claude" else None,
    )
    monkeypatch.setattr("packet_ask.launch.provider_home", lambda _name: tmp_path)
    captured: dict[str, object] = {}

    def fake_run(executable, argv, stdin_text, cwd, env, timeout):  # noqa: ANN001
        captured["env"] = env
        captured["argv"] = argv
        return "ok"

    monkeypatch.setattr("packet_ask.launch.run_isolated_command", fake_run)
    dummy = Packet(root=tmp_path, report=RedactionReport())
    (tmp_path / "packet.md").write_text("hello\n", encoding="utf-8")
    assert launch_claude(dummy, 1) == "ok"
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["ANTHROPIC_API_KEY"] == "anthropic-child-key"
    assert "ANTHROPIC_BASE_URL" not in env
    assert "parent-secret" not in env.values()
    assert "https://evil.example" not in env.values()
    assert env["HOME"] == str(tmp_path)
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert env["DISABLE_ERROR_REPORTING"] == "1"
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "--tools" in argv
    assert "--no-session-persistence" in argv


def test_kimi_passes_key_in_env_not_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PACKET_ASK_KIMI_KEY 는 자식 환경으로만 넘긴다."""
    monkeypatch.setenv("PACKET_ASK_KIMI_KEY", "sk-env-only-key")
    monkeypatch.setattr("packet_ask.launch.require_launchable", lambda _name: None)
    monkeypatch.setattr(
        "packet_ask.launch.resolve_trusted_executable",
        lambda name: Path("/usr/bin/true") if name == "kimi" else None,
    )
    monkeypatch.setattr("packet_ask.launch.provider_home", lambda _name: tmp_path)
    captured: dict[str, dict[str, str]] = {}

    def fake_run(executable, argv, stdin_text, cwd, env, timeout):  # noqa: ANN001
        captured["env"] = env
        return "ok"

    monkeypatch.setattr("packet_ask.launch.run_isolated_command", fake_run)
    dummy = Packet(root=tmp_path, report=RedactionReport())
    (tmp_path / "packet.md").write_text("hello\n", encoding="utf-8")
    assert launch_kimi(dummy, 1) == "ok"
    assert captured["env"]["KIMI_API_KEY"] == "sk-env-only-key"
    config = (tmp_path / "kimi-code" / "config.toml").read_text(encoding="utf-8")
    assert "sk-env-only-key" not in config
