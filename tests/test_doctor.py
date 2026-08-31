"""doctor 플래그 판정."""

import os
import time
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
    import sys

    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "from pathlib import Path\n"
        "print(os.environ.get('ANTHROPIC_API_KEY', 'missing'))\n"
        "print(Path.cwd())\n",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-secret")
    output = doctor._run_help(binary)
    assert output is not None
    assert "parent-secret" not in output
    assert "missing" in output
    assert str(tmp_path) not in output


def test_help_text_is_cached_by_path_and_mtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """같은 실행 파일은 --help 를 한 번만 돌린다."""
    from packet_ask import doctor

    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    calls: list[int] = []

    def fake_run(_path: Path) -> str:
        calls.append(1)
        flags = "--bare\n-p\n--tools\n--permission-mode\n--no-session-persistence\n--setting-sources\n"
        return flags

    monkeypatch.setattr("packet_ask.doctor._run_help", fake_run)
    monkeypatch.setattr("packet_ask.doctor.resolve_trusted_executable", lambda name: binary)
    doctor._help_text("claude")
    doctor._help_text("claude")
    assert len(calls) == 1


def test_help_text_cache_invalidates_when_mtime_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """크기가 같아도 mtime 이 바뀌면 --help 를 다시 돌린다."""
    import os

    binary = tmp_path / "claude"
    body = "#!/bin/sh\n"
    binary.write_text(body, encoding="utf-8")
    binary.chmod(0o700)
    calls: list[int] = []

    def fake_run(_path: Path) -> str:
        calls.append(1)
        return "--bare\n"

    monkeypatch.setattr("packet_ask.doctor._run_help", fake_run)
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
    from packet_ask import doctor

    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    probed: list[list[str]] = []

    def fake_run(path: Path) -> str:
        probed.append([str(path)])
        flags = "--bare\n-p\n--tools\n--permission-mode\n--no-session-persistence\n--setting-sources\n"
        return flags

    monkeypatch.setattr("packet_ask.doctor._run_help", fake_run)
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


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_help_probe_rejects_oversized_output(
    stream: str, tmp_path: Path
) -> None:
    """stdout/stderr 어느 쪽도 합산 help 한도를 넘겨 메모리를 키울 수 없다."""
    import sys

    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"target = sys.{stream}.buffer\n"
        f"target.write(b'x' * ({doctor._HELP_OUTPUT_BYTES} + 1))\n"
        "target.flush()\n",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    assert doctor._run_help(binary) is None


def test_help_probe_combines_streams_and_ignores_returncode(tmp_path: Path) -> None:
    """기존처럼 stdout 뒤 stderr를 합치고 help 자체의 종료 코드는 강제하지 않는다."""
    import sys

    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "sys.stdout.write('--bare\\n')\n"
        "sys.stderr.write('--tools\\n')\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    assert doctor._run_help(binary) == "--bare\n--tools\n"


def test_help_probe_timeout_kills_descendant_after_leader_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """리더가 끝나도 pipe를 잡은 descendant를 deadline 뒤 프로세스 그룹으로 끝낸다."""
    import sys

    pid_file = tmp_path / "descendant.pid"
    ready_file = tmp_path / "descendant.ready"
    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "import signal\n"
        "import time\n"
        "from pathlib import Path\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"    Path({str(ready_file)!r}).write_text('ready')\n"
        "    time.sleep(60)\n"
        f"Path({str(pid_file)!r}).write_text(str(pid))\n"
        f"while not Path({str(ready_file)!r}).is_file():\n"
        "    time.sleep(0.01)\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    monkeypatch.setattr(doctor, "_HELP_TIMEOUT_SECONDS", 1.0)
    started = time.monotonic()
    assert doctor._run_help(binary) is None
    assert time.monotonic() - started < 3
    assert pid_file.is_file()
    descendant_pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(descendant_pid, 0)
        except OSError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("help probe descendant survived process-group cleanup")
