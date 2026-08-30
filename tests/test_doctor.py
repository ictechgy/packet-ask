"""doctor 플래그 판정."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from packet_ask import doctor
from packet_ask.doctor import claude_supports_isolated_print, kimi_supports_print


@pytest.fixture(autouse=True)
def clear_help_cache() -> Iterator[None]:
    """프로세스 안 --help 캐시가 테스트 사이에 남지 않게 한다."""
    doctor._HELP_CACHE.clear()
    yield
    doctor._HELP_CACHE.clear()


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


def test_failed_help_probe_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """일시적 timeout 결과를 프로세스 수명 동안 missing으로 박제하지 않는다."""
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    calls = 0

    def failed(_path: Path) -> None:
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr("packet_ask.doctor.resolve_trusted_executable", lambda _name: binary)
    monkeypatch.setattr("packet_ask.doctor._run_help", failed)
    assert doctor._help_text("claude") is None
    assert doctor._help_text("claude") is None
    assert calls == 2


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


def test_help_text_is_cached_by_path_and_mtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """같은 실행 파일은 --help 를 한 번만 돌린다."""
    import subprocess

    from packet_ask import doctor

    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    calls: list[int] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        flags = "--bare\n-p\n--tools\n--permission-mode\n--no-session-persistence\n--setting-sources\n"
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=flags, stderr="")

    monkeypatch.setattr("packet_ask.doctor.subprocess.run", fake_run)
    monkeypatch.setattr("packet_ask.doctor.resolve_trusted_executable", lambda name: binary)
    doctor._help_text("claude")
    doctor._help_text("claude")
    assert len(calls) == 1


def test_help_text_cache_invalidates_when_mtime_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """크기가 같아도 mtime 이 바뀌면 --help 를 다시 돌린다."""
    import os
    import subprocess

    binary = tmp_path / "claude"
    body = "#!/bin/sh\n"
    binary.write_text(body, encoding="utf-8")
    binary.chmod(0o700)
    calls: list[int] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="--bare\n", stderr="")

    monkeypatch.setattr("packet_ask.doctor.subprocess.run", fake_run)
    monkeypatch.setattr("packet_ask.doctor.resolve_trusted_executable", lambda name: binary)
    doctor._help_text("claude")
    os.utime(binary, ns=(binary.stat().st_atime_ns, binary.stat().st_mtime_ns + 1_000_000))
    doctor._help_text("claude")
    assert binary.read_text(encoding="utf-8") == body
    assert len(calls) == 2


def test_inspect_providers_probes_shared_claude_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """glm 과 claude 는 같은 claude 바이너리를 한 번만 프로브한다."""
    import subprocess

    from packet_ask import doctor

    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    probed: list[list[str]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        probed.append(list(args[0]) if args else [])
        flags = "--bare\n-p\n--tools\n--permission-mode\n--no-session-persistence\n--setting-sources\n"
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=flags, stderr="")

    monkeypatch.setattr("packet_ask.doctor.subprocess.run", fake_run)
    monkeypatch.setattr(
        "packet_ask.doctor.resolve_trusted_executable",
        lambda name: binary if name == "claude" else None,
    )
    statuses = doctor.inspect_providers()
    claude_probes = [item for item in probed if item and item[0].endswith("claude")]
    assert len(claude_probes) == 1
    names = {item.name for item in statuses}
    assert {"paste", "glm", "kimi", "claude", "grok", "agy"} <= names
    assert "glm" in names and "claude" in names
