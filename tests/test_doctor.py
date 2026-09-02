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
    help_text = "--bare\n-p\n--tools\n--permission-mode\n--no-session-persistence\n--setting-sources\n--mcp-config\n--strict-mcp-config\n"
    assert claude_supports_isolated_print(help_text) is True


def test_claude_help_missing_bare() -> None:
    """--bare 가 없으면 실행 후보가 아니다."""
    help_text = "--tools\n--no-session-persistence\n--setting-sources\n"
    assert claude_supports_isolated_print(help_text) is False


def test_claude_help_requires_strict_explicit_mcp_flags() -> None:
    """빈 MCP config만 쓰려면 config와 strict flag가 모두 필요하다."""
    base = "--bare\n-p\n--tools\n--permission-mode\n--no-session-persistence\n--setting-sources\n"
    assert claude_supports_isolated_print(base + "--mcp-config\n") is False
    assert claude_supports_isolated_print(base + "--strict-mcp-config\n") is False


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
        flags = "--bare\n-p\n--tools\n--permission-mode\n--no-session-persistence\n--setting-sources\n--mcp-config\n--strict-mcp-config\n"
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


def test_help_text_cache_invalidates_when_inode_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """path·mtime·size가 같아도 inode 교체면 help를 다시 probe한다."""
    binary = tmp_path / "claude"
    replacement = tmp_path / "replacement"
    body = "#!/bin/sh\n"
    binary.write_text(body, encoding="utf-8")
    binary.chmod(0o700)
    original = binary.stat()
    calls: list[int] = []

    def fake_run(_path: Path) -> str:
        calls.append(1)
        return "--bare\n"

    monkeypatch.setattr("packet_ask.doctor._run_help", fake_run)
    monkeypatch.setattr("packet_ask.doctor.resolve_trusted_executable", lambda _name: binary)
    doctor._help_text("claude")
    replacement.write_text(body, encoding="utf-8")
    replacement.chmod(0o700)
    os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
    replacement.replace(binary)
    doctor._help_text("claude")
    assert len(calls) == 2
    assert len(doctor._HELP_CACHE) == 1


def test_doctor_distinguishes_untrusted_candidate_from_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """권한 검증 실패를 missing으로 오진하거나 실제 경로를 출력하지 않는다."""
    monkeypatch.setattr("packet_ask.doctor._help_text", lambda _name: None)
    monkeypatch.setattr(
        "packet_ask.doctor.trusted_executable_candidate_exists",
        lambda _name: True,
    )
    status = doctor.inspect_provider("glm")
    assert status.installed is True
    assert status.can_launch is False
    assert status.note == "claude CLI exists but failed trusted owner/mode validation."


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
        flags = "--bare\n-p\n--tools\n--permission-mode\n--no-session-persistence\n--setting-sources\n--mcp-config\n--strict-mcp-config\n"
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
    import subprocess
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

    def timeout_after_ready(*_args: object, **_kwargs: object):  # noqa: ANN202
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if pid_file.is_file() and ready_file.is_file():
                raise subprocess.TimeoutExpired("help fixture", 1)
            time.sleep(0.01)
        pytest.fail("help descendant fixture did not become ready")

    monkeypatch.setattr(doctor, "_HELP_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(doctor.select, "select", timeout_after_ready)
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


def test_doctor_signals_are_fixed_constants_not_computed() -> None:
    """doctor 한계 공개도 산출값이 아니라 코드 상수여야 드리프트하지 않는다.

    `GUARANTEES` 와 같은 이유다. 검사 로직이 바뀌어도 상수는 스스로 강해지지
    않아야 한다.
    """
    from types import MappingProxyType

    from packet_ask.doctor import DOCTOR_SIGNALS

    assert isinstance(DOCTOR_SIGNALS, MappingProxyType)
    assert dict(DOCTOR_SIGNALS) == {
        "verification": "flags-mentioned",
        "sandbox": "none",
        "signatures": "not-checked",
    }
    with pytest.raises(TypeError):
        DOCTOR_SIGNALS["sandbox"] = "enforced"  # type: ignore[index]


def test_doctor_output_states_its_own_limits(capsys: pytest.CaptureFixture[str]) -> None:
    """한계는 그것을 근거로 판단하기 전에 도착해야 한다.

    `guarantees` 는 성공한 task 의 영수증에만 붙는다. 그런데 사람이 이 도구를
    믿을지 정하는 첫 표면은 설치 직후의 `doctor` 다. 상쇄해야 할 신호보다
    상쇄가 늦게 오면 안 된다.
    """
    from packet_ask.cli import main

    from packet_ask.doctor import format_doctor_signals_line

    assert main(["doctor"]) == 0
    lines = capsys.readouterr().out.splitlines()
    # 프로바이더 줄이 전부 성공 신호다. 상쇄는 그 뒤에 와야 하므로 위치까지
    # 고정한다. 존재만 보면 루프 위로 옮겨도 통과한다.
    assert lines[-1] == format_doctor_signals_line()
    assert len(lines) > 1


def test_doctor_signal_line_is_append_only_tokens() -> None:
    """영수증과 같은 규약이다. 줄 끝에 정규식을 앵커하지 않게 토큰 나열로 둔다.

    줄 내용이 매핑의 삽입 순서를 따르므로 정확한 문자열로 고정한다. 상수를
    알파벳 정렬하는 리팩터 하나가 append-only 표면의 기존 토큰 순서를 바꾸고
    README 의 예시와 어긋나게 만든다. 순서 무시 비교만으로는 그것을 못 잡는다.
    """
    from packet_ask.doctor import format_doctor_signals_line

    line = format_doctor_signals_line()
    assert line == (
        "packet-ask doctor signals="
        "verification:flags-mentioned,sandbox:none,signatures:not-checked"
    )
    assert "\n" not in line
    assert line.isascii()


def test_doctor_verification_signal_matches_real_behaviour() -> None:
    """`verification: flags-mentioned` 는 기전이 있다는 긍정 서술이다.

    부정문 키와 달리 이 값은 구현이 회귀하면 기계 판독 가능한 거짓이 된다.
    실제로 help 텍스트의 플래그 언급만 보고, 없으면 실행 후보에서 빠지는지
    함께 고정한다.
    """
    from packet_ask.doctor import DOCTOR_SIGNALS, has_cli_flag

    assert DOCTOR_SIGNALS["verification"] == "flags-mentioned"
    complete = (
        "--bare\n-p\n--tools\n--permission-mode\n--no-session-persistence\n"
        "--setting-sources\n--mcp-config\n--strict-mcp-config\n"
    )
    assert has_cli_flag(complete, "--bare") is True
    assert claude_supports_isolated_print(complete) is True
    # 언급이 사라지면 실행 후보에서 빠진다. 언급만 본다는 것이 그 뜻이다.
    assert claude_supports_isolated_print(complete.replace("--bare\n", "")) is False


def test_missing_flag_mention_removes_launch_candidacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """술어 함수가 아니라 `inspect_provider` 결로까지 고정한다.

    술어만 단언하면 launch 게이트가 이 함수를 우회하도록 리팩터되거나 게이트
    없는 신규 launch 프로바이더가 추가돼도 테스트가 전부 녹색인 채
    `verification: flags-mentioned` 만 기계 판독 가능한 거짓이 된다. 기존
    `can_launch is False` 단언은 실행파일 미신뢰 경로뿐이고 이 경로가 아니었다.
    """
    from packet_ask.doctor import DOCTOR_SIGNALS
    from packet_ask.text import message

    complete = (
        "--bare\n-p\n--tools\n--permission-mode\n--no-session-persistence\n"
        "--setting-sources\n--mcp-config\n--strict-mcp-config\n"
    )
    assert DOCTOR_SIGNALS["verification"] == "flags-mentioned"

    monkeypatch.setattr("packet_ask.doctor._help_text", lambda _name: complete)
    assert doctor.inspect_provider("glm").can_launch is True

    # help 는 정상인데 언급 하나가 사라지면 paste 로 강등된다.
    monkeypatch.setattr(
        "packet_ask.doctor._help_text",
        lambda _name: complete.replace("--strict-mcp-config\n", ""),
    )
    degraded = doctor.inspect_provider("glm")
    assert degraded.installed is True
    assert degraded.can_launch is False
    assert degraded.note == message("launch_flags_missing")

    # kimi 도 같은 게이트를 지난다. Claude 계열만 묶여 있으면 안 된다.
    monkeypatch.setattr(
        "packet_ask.doctor._help_text",
        lambda _name: "--quiet\n--agent-file\n--work-dir\n--skills-dir\n",
    )
    assert doctor.inspect_provider("kimi").can_launch is True
    monkeypatch.setattr(
        "packet_ask.doctor._help_text",
        lambda _name: "--quiet\n--agent-file\n--work-dir\n",
    )
    assert doctor.inspect_provider("kimi").can_launch is False


def test_doctor_does_not_create_a_sandbox_or_check_signatures() -> None:
    """`sandbox: none` / `signatures: not-checked` 가 실제 코드와 맞는지 본다.

    doctor 가 언젠가 진짜 샌드박스나 서명 검증을 하게 되면 이 테스트가 먼저
    깨져서 상수를 같이 고치도록 만든다.
    """
    import inspect as _inspect

    from packet_ask import doctor as _doctor
    from packet_ask.doctor import DOCTOR_SIGNALS

    assert DOCTOR_SIGNALS["sandbox"] == "none"
    assert DOCTOR_SIGNALS["signatures"] == "not-checked"
    source = _inspect.getsource(_doctor)
    # 샌드박스를 만드는 호출도, 서명·해시를 확인하는 호출도 없다. 이 단언은
    # doctor.py 모듈 본문 안에서만 유효하다. 기능이 다른 모듈로 옮겨 가면
    # 잡지 못한다. 다만 그때 어긋나는 방향은 "하는데 안 한다고 밝힘" 이라
    # 과잉 신뢰를 만들지 않는다.
    for absent in (
        "sandbox-exec",
        "sandbox_init",
        "landlock",
        "bwrap",
        "codesign",
        "spctl",
        "gpg",
        "openssl",
        "hashlib",
        "sha256",
    ):
        assert absent not in source
