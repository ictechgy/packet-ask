"""doctor 플래그 판정."""

from pathlib import Path

import pytest

from packet_ask.doctor import claude_supports_isolated_print, kimi_supports_print


def test_claude_help_with_required_flags() -> None:
    """필수 플래그가 있으면 실행 후보가 된다."""
    help_text = "--bare\n-p\n--tools\n--permission-mode\n--no-session-persistence\n--setting-sources\n"
    assert claude_supports_isolated_print(help_text) is True


def test_claude_help_missing_bare() -> None:
    """--bare 가 없으면 실행 후보가 아니다."""
    help_text = "--tools\n--no-session-persistence\n--setting-sources\n"
    assert claude_supports_isolated_print(help_text) is False


def test_kimi_detects_prompt_flag() -> None:
    """-p 가 있으면 print 모드는 있다."""
    assert kimi_supports_print("kimi -p prompt") is True


def test_kimi_isolated_print_needs_agent_file_and_workdir() -> None:
    """도구 없는 원샷은 agent-file 과 work-dir 이 필요하다."""
    from packet_ask.doctor import kimi_supports_isolated_print

    full = "--quiet\n--agent-file\n--work-dir\n--skills-dir\n"
    assert kimi_supports_isolated_print(full) is True
    assert kimi_supports_isolated_print("--prompt\n--quiet\n") is False
    assert kimi_supports_isolated_print("-p\n--agent-file\n--work-dir\n") is False


def test_kimi_print_flag_not_confused_with_permission() -> None:
    """-p 부분문자열은 --permission-mode 에 오탐하지 않는다."""
    from packet_ask.doctor import kimi_supports_print

    assert kimi_supports_print("--permission-mode\n--path") is False
    assert kimi_supports_print("-p\n--prompt") is True


def test_help_probe_does_not_inherit_parent_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--help 프로브는 부모 키를 물려주지 않고 timeout 을 둔다."""
    import subprocess
    from pathlib import Path as PathType

    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["env"] = kwargs.get("env")
        captured["timeout"] = kwargs.get("timeout")
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="--quiet\n", stderr="")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-secret")
    monkeypatch.setattr("packet_ask.doctor.subprocess.run", fake_run)
    monkeypatch.setattr(
        "packet_ask.doctor.resolve_trusted_executable",
        lambda name: PathType("/usr/bin/true"),
    )
    from packet_ask.doctor import _help_text

    _help_text("claude")
    env = captured["env"]
    assert isinstance(env, dict)
    assert "parent-secret" not in env.values()
    assert "ANTHROPIC_API_KEY" not in env
    assert captured["timeout"] == 10
    assert captured["cwd"] is not None
