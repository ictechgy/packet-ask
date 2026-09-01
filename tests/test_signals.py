"""task 종료 signal의 handler 수명과 cleanup 연결."""

from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask import cli
from packet_ask import doctor
from packet_ask import launch
from packet_ask import scope
from packet_ask.cli import main
from packet_ask.signals import task_signal_handlers


def _init_repo(root: Path) -> Path:
    """signal CLI 테스트용 최소 git 저장소."""
    import subprocess

    root.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=root, check=True)
    source = root / "app.py"
    source.write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    return root


@pytest.mark.parametrize(
    ("item", "expected"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143), (signal.SIGHUP, 129)],
)
def test_task_signal_handler_uses_shell_exit_code_and_restores(
    item: signal.Signals, expected: int
) -> None:
    """task 범위에서만 128+signum을 쓰고 기존 handler를 복구한다."""
    previous = signal.getsignal(item)
    with task_signal_handlers():
        handler = signal.getsignal(item)
        assert callable(handler)
        with pytest.raises(SystemExit) as exc:
            handler(item, None)
        assert exc.value.code == expected
    assert signal.getsignal(item) == previous


@pytest.mark.parametrize(
    ("item", "expected"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143), (signal.SIGHUP, 129)],
)
def test_signal_after_packet_build_cleans_assigned_packet(
    item: signal.Signals,
    expected: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build 반환 직전 signal도 assignment 뒤 전달되어 packet을 남기지 않는다."""
    repo = _init_repo(tmp_path / "repo")
    cache_parent = tmp_path / "cache" / "packet-ask"
    real_build = cli.build_packet

    def build_then_signal(*args: object, **kwargs: object):  # noqa: ANN202
        packet = real_build(*args, **kwargs)
        os.kill(os.getpid(), item)
        return packet

    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_CACHE_DIR", str(cache_parent))
    monkeypatch.setattr(cli, "build_packet", build_then_signal)
    previous = signal.getsignal(item)
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "review",
                "--provider",
                "paste",
                "--files",
                "app.py",
                "--question",
                "review",
            ]
        )
    assert exc.value.code == expected
    leftovers = list(cache_parent.glob("packet-ask-*")) if cache_parent.exists() else []
    assert leftovers == []
    assert signal.getsignal(item) == previous


def test_sigterm_during_provider_kills_group_and_cleans_packet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """provider wait 중 SIGTERM은 child group을 끝내고 packet cleanup 뒤 143을 유지한다."""
    import subprocess

    repo = _init_repo(tmp_path / "repo")
    cache_parent = tmp_path / "cache" / "packet-ask"
    provider = tmp_path / "provider.sh"
    provider.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    provider.chmod(0o700)
    holder: dict[str, subprocess.Popen[str]] = {}
    real_spawn = launch._spawn_isolated
    real_select = launch.select.select

    def spy(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        process = real_spawn(*args, **kwargs)  # type: ignore[arg-type]
        holder["process"] = process
        return process

    sent = False

    def terminate(*args: object, **kwargs: object):  # noqa: ANN202
        nonlocal sent
        if not sent:
            sent = True
            os.kill(os.getpid(), signal.SIGTERM)
        return real_select(*args, **kwargs)

    def execute(_provider: str, packet, timeout: int, _source: str) -> str:  # noqa: ANN001
        monkeypatch.setattr(launch.select, "select", terminate)
        return launch.run_isolated_command(
            provider,
            [],
            packet.payload_text(),
            packet.root,
            launch.isolated_env(tmp_path / "provider-home", {}),
            timeout,
        )

    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_CACHE_DIR", str(cache_parent))
    monkeypatch.setattr(launch, "_spawn_isolated", spy)
    monkeypatch.setattr(cli, "_execute_provider", execute)
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "review",
                "--provider",
                "paste",
                "--files",
                "app.py",
                "--question",
                "review",
            ]
        )
    assert exc.value.code == 143
    assert holder["process"].poll() is not None
    leftovers = list(cache_parent.glob("packet-ask-*")) if cache_parent.exists() else []
    assert leftovers == []


def test_non_task_command_does_not_install_signal_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """providers 같은 로컬 조회 명령은 process handler를 건드리지 않는다."""
    previous = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(cli, "_run_providers", lambda _json: codes.SUCCESS)
    assert main(["providers"]) == codes.SUCCESS
    assert signal.getsignal(signal.SIGTERM) == previous


def test_provider_spawn_publication_is_signal_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spawn이 반환하자마자 TERM이 와도 등록된 provider group을 회수한다."""
    import subprocess

    provider = tmp_path / "provider.sh"
    provider.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    provider.chmod(0o700)
    holder: dict[str, subprocess.Popen[str]] = {}
    real_spawn = launch._spawn_isolated

    def spawn_then_signal(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        process = real_spawn(*args, **kwargs)  # type: ignore[arg-type]
        holder["process"] = process
        os.kill(os.getpid(), signal.SIGTERM)
        return process

    monkeypatch.setattr(launch, "_spawn_isolated", spawn_then_signal)
    with task_signal_handlers():
        with pytest.raises(SystemExit) as exc:
            launch.run_isolated_command(
                provider,
                [],
                "",
                tmp_path,
                launch.isolated_env(tmp_path / "home", {}),
                5,
            )
    assert exc.value.code == 143
    assert holder["process"].poll() is not None


def test_provider_spawn_publication_defers_sigint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Popen 반환 직후 Ctrl-C도 proc 할당 뒤 130 cleanup 경로로 전달한다."""
    import subprocess

    provider = tmp_path / "provider.sh"
    provider.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    provider.chmod(0o700)
    holder: dict[str, subprocess.Popen[str]] = {}
    real_spawn = launch._spawn_isolated

    def spawn_then_interrupt(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        process = real_spawn(*args, **kwargs)  # type: ignore[arg-type]
        holder["process"] = process
        os.kill(os.getpid(), signal.SIGINT)
        return process

    monkeypatch.setattr(launch, "_spawn_isolated", spawn_then_interrupt)
    with task_signal_handlers():
        with pytest.raises(SystemExit) as exc:
            launch.run_isolated_command(
                provider,
                [],
                "",
                tmp_path,
                launch.isolated_env(tmp_path / "home", {}),
                5,
            )
    assert exc.value.code == 130
    assert holder["process"].poll() is not None


def test_git_spawn_publication_is_signal_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git Popen 반환 직후 HUP도 proc 등록 뒤 기존 group cleanup으로 전달한다."""
    import subprocess

    executable = tmp_path / "git"
    executable.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    executable.chmod(0o700)
    holder: dict[str, subprocess.Popen[bytes]] = {}
    real_popen = scope.subprocess.Popen

    def spawn_then_signal(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)  # type: ignore[arg-type]
        holder["process"] = process
        os.kill(os.getpid(), signal.SIGHUP)
        return process

    monkeypatch.setattr(scope, "_git_executable", lambda: str(executable))
    monkeypatch.setattr(scope.subprocess, "Popen", spawn_then_signal)
    with task_signal_handlers():
        with pytest.raises(SystemExit) as exc:
            scope.run_bounded_git(tmp_path, [], 4096)
    assert exc.value.code == 129
    assert holder["process"].poll() is not None


def test_help_spawn_publication_is_signal_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """help probe spawn 직후 TERM도 process group을 남기지 않는다."""
    import subprocess

    executable = tmp_path / "claude"
    executable.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    executable.chmod(0o700)
    holder: dict[str, subprocess.Popen[bytes]] = {}
    real_popen = doctor.subprocess.Popen

    def spawn_then_signal(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)  # type: ignore[arg-type]
        holder["process"] = process
        os.kill(os.getpid(), signal.SIGTERM)
        return process

    monkeypatch.setattr(doctor.subprocess, "Popen", spawn_then_signal)
    with task_signal_handlers():
        with pytest.raises(SystemExit) as exc:
            doctor._run_help(executable)
    assert exc.value.code == 143
    assert holder["process"].poll() is not None


def test_spawn_does_not_leave_task_signals_blocked_in_child(tmp_path: Path) -> None:
    """atomic publication이 provider child의 TERM/HUP mask를 바꾸지 않는다."""
    import sys

    executable = tmp_path / "provider"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import signal\n"
        "blocked = signal.pthread_sigmask(signal.SIG_BLOCK, set())\n"
        "print(int(signal.SIGINT in blocked or signal.SIGTERM in blocked or signal.SIGHUP in blocked))\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    with task_signal_handlers():
        output = launch.run_isolated_command(
            executable,
            [],
            "",
            tmp_path,
            launch.isolated_env(tmp_path / "home", {}),
            5,
        )
    assert output == "0\n"
