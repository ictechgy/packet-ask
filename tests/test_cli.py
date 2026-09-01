"""CLI 종료 코드와 paste 출력."""

import argparse
import io
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask import cli
from packet_ask.cli import main
from packet_ask.cli import _parser, _read_question_stdin
from packet_ask.deadline import Deadline
from packet_ask.errors import BudgetError, PacketAskError
from packet_ask.keysource import CredentialStatus
from packet_ask.lifecycle import PACKET_LEASE_NAME, STALE_PACKET_SECONDS, close_packet_lease
from packet_ask.packet import build_packet
from packet_ask.text import message


def _init_repo(root: Path) -> Path:
    """테스트용 저장소를 만든다."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=root, check=True)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/app.py"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    return root


def test_task_parser_exposes_preflight_timeout() -> None:
    """task preflight timeout은 기본 30초이고 명시값을 그대로 받는다."""
    default = _parser().parse_args(["review", "--provider", "paste", "--files", "a.py"])
    explicit = _parser().parse_args(
        [
            "inspect",
            "review",
            "--files",
            "a.py",
            "--preflight-timeout",
            "7",
        ]
    )
    assert default.preflight_timeout == 30
    assert explicit.preflight_timeout == 7


def test_question_stdin_obeys_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """닫히지 않은 pipe는 provider 시작 전 bounded budget 오류로 끝난다."""
    read_fd, write_fd = os.pipe()
    stream = io.TextIOWrapper(os.fdopen(read_fd, "rb"), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", stream)
    try:
        with pytest.raises(BudgetError, match="preflight"):
            _read_question_stdin(1024, Deadline.after(0.01))
    finally:
        os.close(write_fd)
        stream.close()


def test_question_stdin_requires_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    """locale 대신 UTF-8 byte contract를 쓰고 잘못된 입력은 stable usage로 닫는다."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"\xff\xfe")
    os.close(write_fd)
    stream = io.TextIOWrapper(os.fdopen(read_fd, "rb"), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", stream)
    try:
        with pytest.raises(PacketAskError) as exc:
            _read_question_stdin(1024, Deadline.after(1))
    finally:
        stream.close()
    assert exc.value.code == codes.USAGE


def test_review_paste_prints_untrusted_packet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """paste 리뷰는 패킷을 봉투에 넣어 출력한다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--question",
            "이 코드의 문제를 찾아줘",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert "UNTRUSTED PROVIDER OUTPUT" in captured.out
    assert "print(1)" in captured.out
    leftover = repo / ".packet-ask-tmp"
    assert not leftover.exists()


def test_review_line_numbers_reach_task_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """task parser의 opt-in flag가 실제 provider payload 렌더까지 전달된다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_LANG", "en")
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--line-numbers",
            "--question",
            "review this code",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert "Packet-local line numbers" in captured.out
    assert "1 | print(1)" in captured.out


def test_review_selected_tree_reaches_task_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """명시 파일 tree는 provider payload에 들어가고 sibling을 자동 수집하지 않는다."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "unselected.py").write_text("unique sibling\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_LANG", "en")
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--selected-tree",
            "--question",
            "review this code",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert "Selected file tree" in captured.out
    assert "```text\nsrc/\n  app.py\n```" in captured.out
    assert "unselected.py" not in captured.out
    assert "unique sibling" not in captured.out


@pytest.mark.parametrize(
    "argv",
    [
        ["review", "--provider", "paste", "--diff", "HEAD", "--selected-tree"],
        ["research", "--provider", "paste", "--question", "research", "--selected-tree"],
    ],
)
def test_selected_tree_requires_explicit_files(argv: list[str]) -> None:
    """diff나 question-only packet에서 tree flag가 조용한 no-op이 되지 않는다."""
    assert main(argv) == codes.USAGE


def test_inspect_diff_selected_tree_is_rejected() -> None:
    """inspect parser에서도 diff-only tree가 조용히 무시되지 않는다."""
    assert (
        main(["inspect", "review", "--diff", "HEAD", "--selected-tree"])
        == codes.USAGE
    )


def test_rejects_implementation_without_vendor(monkeypatch: pytest.MonkeyPatch) -> None:
    """구현 요청은 벤더 없이 거절한다."""
    code = main(
        ["review", "--provider", "paste", "--question", "이 기능을 구현해줘"]
    )
    assert code == codes.POLICY


def test_research_requires_question() -> None:
    """research 는 질문이 없으면 usage 오류."""
    code = main(["research", "--provider", "paste"])
    assert code == codes.USAGE


def test_non_review_modes_remain_question_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """shared pipeline으로 수렴해도 review 외 mode에 scope를 새로 강제하지 않는다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    argv = ["research", "--provider", "paste", "--question", "research this question"]
    assert main(argv) == codes.SUCCESS


@pytest.mark.parametrize("command", ["brainstorm", "paste"])
def test_undocumented_task_commands_are_gone(command: str) -> None:
    """문서에 없던 task mode는 파서 단계에서 사라졌다. provider paste 는 남는다."""
    # --provider 를 채워야 "provider 누락" 때문에 통과하는 헛된 성공을 막는다.
    with pytest.raises(SystemExit) as excinfo:
        _parser().parse_args([command, "--provider", "paste", "--question", "t"])
    assert excinfo.value.code == codes.USAGE


def test_task_commands_are_exactly_review_and_research() -> None:
    """CLI task 표면이 문서화된 두 mode 와 일치하는지 고정한다."""
    actions = [
        action
        for action in _parser()._subparsers._group_actions  # type: ignore[union-attr]
        if isinstance(action, argparse._SubParsersAction)
    ]
    commands = set(actions[0].choices)
    assert {"review", "research"} <= commands
    assert "brainstorm" not in commands
    assert "paste" not in commands


def test_provider_is_required_for_every_task_command() -> None:
    """paste 커맨드가 사라졌으므로 --provider 는 모든 task 에서 필수다."""
    for command in ("review", "research"):
        with pytest.raises(SystemExit) as excinfo:
            _parser().parse_args([command, "--question", "t"])
        assert excinfo.value.code == codes.USAGE


def test_policy_rejects_before_provider_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """공통 input 단계는 금지 질문을 provider catalog보다 먼저 거절한다."""
    monkeypatch.setattr(
        cli,
        "lookup_provider",
        lambda _provider: (_ for _ in ()).throw(
            AssertionError("provider lookup must not happen")
        ),
    )
    assert main(
        ["review", "--provider", "paste", "--question", "이 기능을 구현해줘"]
    ) == codes.POLICY


@pytest.mark.parametrize(
    ("packet_bytes", "expected"),
    [
        (1, 1200),
        (64 * 1024, 1200),
        (64 * 1024 + 1, 1500),
        (128 * 1024, 1500),
        (128 * 1024 + 1, 1800),
        (256 * 1024, 1800),
    ],
)
def test_auto_timeout_uses_generous_packet_size_tiers(
    packet_bytes: int, expected: int
) -> None:
    """크기 tier 경계는 결정적이고 관측 최대보다 넉넉하다."""
    assert cli._resolve_timeout(None, packet_bytes) == (expected, "auto")


@pytest.mark.parametrize("requested", [1, 300, 3600])
def test_explicit_timeout_is_never_clamped(requested: int) -> None:
    """명시 timeout은 packet 크기와 무관하게 정확히 존중한다."""
    assert cli._resolve_timeout(requested, 256 * 1024) == (requested, "explicit")


@pytest.mark.parametrize(
    ("command", "files", "include_files"),
    [
        ("research", [Path("a.py")], []),
        ("review", [], [Path("a.py")]),
        ("future-mode", [], [Path("a.py")]),
    ],
)
def test_collect_scope_rejects_wrong_mode_file_flags(
    tmp_path: Path,
    command: str,
    files: list[Path],
    include_files: list[Path],
) -> None:
    """policy 호출 순서가 바뀌어도 scope 계층이 잘못된 플래그를 버리지 않는다."""
    args = argparse.Namespace(
        command=command,
        files=files,
        include_files=include_files,
        max_files=25,
        max_bytes=256 * 1024,
        staged=False,
        diff=None,
        unstaged=False,
    )
    with pytest.raises(PacketAskError):
        cli._collect_scope(args, tmp_path)


def test_include_files_is_never_silently_dropped(tmp_path: Path) -> None:
    """research 만 --include-files 를 소비한다. 새 mode 가 생겨도 조용히 버리지 않는다."""
    args = argparse.Namespace(
        command="future-mode",
        files=[Path("a.py")],
        include_files=[Path("b.py")],
        max_files=25,
        max_bytes=256 * 1024,
        staged=False,
        diff=None,
        unstaged=False,
    )
    with pytest.raises(PacketAskError) as excinfo:
        cli._collect_scope(args, tmp_path)
    assert str(excinfo.value) == message("include_files_mode")


def test_review_include_files_keeps_its_own_message(tmp_path: Path) -> None:
    """일반 가드를 위로 올려도 review 전용 문구가 조용히 바뀌지 않게 고정한다."""
    args = argparse.Namespace(
        command="review",
        files=[],
        include_files=[Path("a.py")],
        max_files=25,
        max_bytes=256 * 1024,
        staged=False,
        diff=None,
        unstaged=False,
    )
    with pytest.raises(PacketAskError) as excinfo:
        cli._collect_scope(args, tmp_path)
    assert str(excinfo.value) == message("review_include_files")


def test_claude_without_dedicated_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """claude 서브는 전역 Anthropic 키를 쓰지 않는다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("PACKET_ASK_CLAUDE_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-used")
    code = main(
        [
            "review",
            "--provider",
            "claude",
            "--credential-source",
            "env",
            "--files",
            "src/app.py",
            "--question",
            "이 코드를 리뷰해줘",
        ]
    )
    assert code in {codes.PROVIDER_MISSING, codes.CONFINEMENT}


def test_glm_without_dedicated_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GLM은 전역 Anthropic 키가 아니라 PACKET_ASK_GLM_KEY 만 받는다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("PACKET_ASK_GLM_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-used")
    code = main(
        [
            "review",
            "--provider",
            "glm",
            "--credential-source",
            "env",
            "--files",
            "src/app.py",
            "--question",
            "이 코드를 리뷰해줘",
        ]
    )
    assert code in {codes.PROVIDER_MISSING, codes.CONFINEMENT}


def test_kimi_without_cli_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kimi 바이너리가 없으면 벤더를 실행하지 않는다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "packet_ask.launch.resolve_trusted_executable",
        lambda name: None if name == "kimi" else __import__("packet_ask.paths", fromlist=["resolve_trusted_executable"]).resolve_trusted_executable(name),
    )
    code = main(
        [
            "review",
            "--provider",
            "kimi",
            "--files",
            "src/app.py",
            "--question",
            "이 코드를 리뷰해줘",
        ]
    )
    assert code in {codes.PROVIDER_MISSING, codes.CONFINEMENT}


def test_grok_provider_is_paste_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """grok 는 실행하지 않고 패킷만 출력한다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "grok",
            "--files",
            "src/app.py",
            "--question",
            "이 코드의 문제를 찾아줘",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert "UNTRUSTED PROVIDER OUTPUT" in captured.out
    assert "print(1)" in captured.out


def test_providers_json_lists_builtins(capsys: pytest.CaptureFixture[str]) -> None:
    """providers --json 에 내장 id 가 있다."""
    import json

    code = main(["providers", "--json"])
    assert code == codes.SUCCESS
    rows = json.loads(capsys.readouterr().out)
    ids = {row["id"] for row in rows}
    assert {"paste", "glm", "kimi", "claude", "grok", "agy"} <= ids


def test_unknown_provider_is_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """카탈로그에 없는 id 는 usage."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "nope",
            "--files",
            "src/app.py",
            "--question",
            "이 코드를 리뷰해줘",
        ]
    )
    assert code == codes.USAGE
    assert main(["review", "--provider", "nope", "--question", "review"]) == codes.USAGE


def test_review_without_explicit_scope_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """unstaged 가 있어도 --files/--diff/--staged/--unstaged 없으면 거절한다."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code = main(["review", "--provider", "paste", "--question", "이 변경을 리뷰해줘"])
    assert code == codes.SCOPE


def test_review_unstaged_sends_working_tree_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--unstaged 를 명시하면 워킹 트리 diff 만 보낸다."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--unstaged",
            "--question",
            "이 변경을 리뷰해줘",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert "print(2)" in captured.out


def test_review_include_files_is_not_a_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """review 의 --include-files 는 범위를 만들지 않고 거절한다."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--include-files",
            "src/app.py",
            "--question",
            "이 변경을 리뷰해줘",
        ]
    )
    assert code in {codes.USAGE, codes.SCOPE, codes.POLICY}


def test_research_rejects_local_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """research 는 --diff 로 로컬 코드를 첨부하지 않는다."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code = main(
        [
            "research",
            "--provider",
            "paste",
            "--diff",
            "HEAD",
            "--question",
            "이 변경의 공개 자료를 찾아줘",
        ]
    )
    assert code == codes.POLICY


@pytest.mark.parametrize(
    ("diff", "staged", "unstaged"),
    [
        ("HEAD", False, False),
        ("", False, False),
        (None, True, False),
        (None, False, True),
    ],
)
def test_collect_scope_research_rejects_diff_without_policy_dependency(
    diff: str | None,
    staged: bool,
    unstaged: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """policy 순서가 바뀌어도 scope collector가 research local diff를 열지 않는다."""
    args = argparse.Namespace(
        command="research",
        files=[],
        include_files=[],
        diff=diff,
        staged=staged,
        unstaged=unstaged,
        max_files=25,
        max_bytes=1024,
    )
    monkeypatch.setattr(
        cli,
        "collect_git_diff",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("git diff must not run")
        ),
    )
    with pytest.raises(PacketAskError) as exc:
        cli._collect_scope(args, tmp_path, mode="research")
    assert exc.value.code == codes.USAGE


def test_review_rejects_files_and_diff_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """review 는 스코프 플래그를 하나만 받는다."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--diff",
            "HEAD",
            "--question",
            "리뷰해줘",
        ]
    )
    assert code == codes.SCOPE


def test_review_budget_counts_question_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """질문과 파일을 합친 패킷 예산을 넘기면 거절한다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--max-bytes",
            "40",
            "--question",
            "x" * 80,
        ]
    )
    assert code == codes.BUDGET


def test_review_budget_counts_rendered_packet_overhead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """헤더·경로·계약문을 포함한 실제 packet.md가 예산을 지킨다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--max-bytes",
            "100",
            "--question",
            "q",
        ]
    )
    assert code == codes.BUDGET


def test_review_prints_receipt_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """벤더 전에 보내는 경로를 stderr 로 알려 준다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--question",
            "리뷰해줘",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert "src/app.py" in captured.err
    assert "paste" in captured.err


def test_review_prints_timing_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """성공 시 stderr 에 비밀 없는 구간 시간을 쓴다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_GLM_KEY", "glm-secret-must-not-leak")
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--question",
            "리뷰해줘",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    import re

    timing_lines = [line for line in captured.err.splitlines() if line.startswith("packet-ask timing")]
    assert len(timing_lines) == 1
    assert re.fullmatch(
        r"packet-ask timing preflight_ms=\d+ packet_ms=\d+ launch_ms=\d+ total_ms=\d+",
        timing_lines[0],
    )
    assert "glm-secret-must-not-leak" not in captured.err
    assert "glm-secret-must-not-leak" not in captured.out


def test_explicit_progress_emits_non_sensitive_heartbeat_and_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """긴 launch에서만 fixed heartbeat를 내고 task 종료 뒤 thread를 남기지 않는다."""
    repo = _init_repo(tmp_path)
    secret = "progress-secret-must-not-leak"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_GLM_KEY", secret)
    monkeypatch.setattr(cli, "PROGRESS_INTERVAL_SECONDS", 0.005)

    def slow_provider(*_args: object, **_kwargs: object) -> str:
        time.sleep(0.05)
        return "reviewed"

    monkeypatch.setattr(cli, "_execute_provider", slow_provider)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--question",
            "review",
            "--progress",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    progress = [
        line for line in captured.err.splitlines() if line.startswith("packet-ask progress")
    ]
    assert progress
    assert all("phase=launch elapsed_ms=" in line for line in progress)
    assert secret not in captured.err
    assert secret not in captured.out
    assert not any(item.name == "packet-ask-progress" for item in threading.enumerate())


def test_progress_is_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--progress가 없으면 기존 stderr contract에 heartbeat를 추가하지 않는다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli, "_execute_provider", lambda *_args: "reviewed")
    assert main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--question",
            "review",
        ]
    ) == codes.SUCCESS
    assert "packet-ask progress" not in capsys.readouterr().err


def test_progress_thread_start_failure_does_not_fail_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """optional observability thread를 못 만들면 provider body는 그대로 실행한다."""
    monkeypatch.setattr(
        threading.Thread,
        "start",
        lambda _thread: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    reached: list[bool] = []
    with cli._launch_progress(True, time.monotonic()):
        reached.append(True)
    assert reached == [True]


def test_review_json_includes_timing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json 봉투에 밀리초 구간만 넣고 키 값은 넣지 않는다."""
    import json

    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_KIMI_KEY", "kimi-secret-must-not-leak")
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--json",
            "--question",
            "리뷰해줘",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    data = json.loads(captured.out)
    timing = data["timing"]
    assert set(timing) == {"preflight_ms", "packet_ms", "launch_ms", "total_ms"}
    for key in timing:
        assert isinstance(timing[key], int)
        assert timing[key] >= 0
    assert timing["total_ms"] >= timing["launch_ms"]
    dumped = json.dumps(data)
    assert "kimi-secret-must-not-leak" not in dumped
    assert "kimi-secret-must-not-leak" not in captured.err


def test_review_json_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json 은 versioned 봉투만 stdout 에 쓴다."""
    import json

    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--json",
            "--question",
            "리뷰해줘",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    data = json.loads(captured.out)
    assert data["schema"] == "packet-ask.v1"
    assert data["ok"] is True
    assert data["receipt"]["provider"] == "paste"
    assert "src/app.py" in data["receipt"]["paths"]
    assert "untrusted_output" in data
    assert "print(1)" in data["untrusted_output"]
    assert data["receipt"]["timeout_seconds"] == 1200
    assert data["receipt"]["timeout_source"] == "auto"
    assert data["receipt"]["timeout_applies"] is False


def test_json_parse_error_is_single_generic_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """argparse 오류는 raw argv/usage 없이 stdout JSON 하나로 반환한다."""
    code = main(["review", "--provider", "paste", "--json", "--max-bytes", "bad"])
    captured = capsys.readouterr()
    assert code == codes.USAGE
    assert captured.err == ""
    data = json.loads(captured.out)
    assert data == {
        "schema": "packet-ask.v1",
        "ok": False,
        "error": {
            "code": codes.USAGE,
            "kind": "usage",
            "message": "Invalid command-line arguments.",
        },
    }


def test_json_parse_error_never_reflects_unknown_argument(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """unknown option 뒤 민감해 보이는 원문을 JSON·stderr 어디에도 반사하지 않는다."""
    value = "sk-proj-sensitive-value-that-must-not-appear"
    code = main(["review", "--json", "--unknown-option", value])
    captured = capsys.readouterr()
    assert code == codes.USAGE
    assert value not in captured.out
    assert value not in captured.err
    assert json.loads(captured.out)["error"]["kind"] == "usage"


def test_json_runtime_error_does_not_include_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """scope 예외의 사용자 경로는 실패 envelope에 들어가지 않는다."""
    repo = _init_repo(tmp_path / "repo")
    outside = tmp_path / "private-customer-name.py"
    outside.write_text("print(1)\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            str(outside),
            "--json",
            "--question",
            "review",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SCOPE
    assert str(outside) not in captured.out
    assert str(outside) not in captured.err
    data = json.loads(captured.out)
    assert data["error"]["kind"] == "scope"


def test_json_provider_error_is_generic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """provider 예외 원문은 숨기되 안정된 code와 kind를 보존한다."""
    repo = _init_repo(tmp_path / "repo")
    sensitive = "provider failed at /private/customer/path"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        cli,
        "_execute_provider",
        lambda *_args: (_ for _ in ()).throw(
            PacketAskError(sensitive, codes.PROVIDER_FAILED)
        ),
    )
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--json",
            "--question",
            "review",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.PROVIDER_FAILED
    assert sensitive not in captured.out
    assert sensitive not in captured.err
    data = json.loads(captured.out)
    assert data["error"]["kind"] == "provider_failed"


def test_non_json_parse_and_runtime_errors_keep_existing_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json이 없으면 argparse와 PacketAskError의 기존 text 계약을 유지한다."""
    with pytest.raises(SystemExit) as parse_exit:
        main(["review", "--max-bytes", "bad"])
    assert parse_exit.value.code == codes.USAGE
    assert "usage:" in capsys.readouterr().err

    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    code = main(["review", "--provider", "paste", "--files", "missing.py"])
    captured = capsys.readouterr()
    assert code == codes.SCOPE
    assert "missing.py" in captured.err
    assert captured.out == ""


def test_unexpected_json_failure_has_no_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """예상하지 못한 Exception도 JSON 모드에서는 generic internal 오류로 닫는다."""
    sensitive = "unexpected private detail"
    monkeypatch.setattr(cli, "_run_providers", lambda _json: (_ for _ in ()).throw(RuntimeError(sensitive)))
    code = main(["providers", "--json"])
    captured = capsys.readouterr()
    assert code == codes.INTERNAL
    assert sensitive not in captured.out
    assert sensitive not in captured.err
    assert "Traceback" not in captured.err
    assert json.loads(captured.out)["error"]["kind"] == "internal"


def test_review_paste_uses_cache_dir_not_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """패킷 부모는 OS 캐시이며 레포 안에 만들지 않는다."""
    repo = _init_repo(tmp_path / "repo")
    cache = tmp_path / "cache"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_CACHE_DIR", str(cache))
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--question",
            "이 코드의 문제를 찾아줘",
        ]
    )
    assert code == codes.SUCCESS
    assert not (repo / ".packet-ask-tmp").exists()
    leftovers = list(cache.glob("packet-ask-*")) if cache.exists() else []
    assert leftovers == []


def test_review_reaps_old_unlocked_packet_before_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """새 task는 lease가 풀린 오래된 packet을 먼저 정리한다."""
    repo = _init_repo(tmp_path / "repo")
    cache_parent = tmp_path / "cache" / "packet-ask"
    cache_parent.mkdir(parents=True)
    stale = build_packet("review", "review", [], None, cache_parent)
    stale_root = stale.root
    close_packet_lease(stale._lease_fd)
    stale._lease_fd = None
    old = (stale_root / PACKET_LEASE_NAME).stat().st_mtime - STALE_PACKET_SECONDS - 1
    os.utime(stale_root / PACKET_LEASE_NAME, (old, old))

    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_CACHE_DIR", str(cache_parent))
    assert main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--question",
            "review",
        ]
    ) == codes.SUCCESS
    assert not stale_root.exists()


def test_success_cleanup_failure_emits_no_success_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """packet 삭제 실패 시 성공 stdout을 먼저 내보내지 않는다."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_CACHE_DIR", str(tmp_path / "cache"))

    def fail_destroy(_packet: object) -> None:
        raise OSError("blocked")

    monkeypatch.setattr("packet_ask.packet.Packet.destroy", fail_destroy)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--question",
            "review",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.INTERNAL
    assert captured.out == ""
    assert "temporary packet" in captured.err.lower()


def test_cleanup_failure_does_not_mask_provider_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """provider 실패 뒤 cleanup 오류는 원래 종료 코드를 바꾸지 않는다."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_CACHE_DIR", str(tmp_path / "cache"))

    def fail_provider(*_args: object, **_kwargs: object) -> str:
        raise PacketAskError("provider failed", codes.PROVIDER_FAILED)

    def fail_destroy(_packet: object) -> None:
        raise OSError("blocked")

    monkeypatch.setattr("packet_ask.cli._execute_provider", fail_provider)
    monkeypatch.setattr("packet_ask.packet.Packet.destroy", fail_destroy)
    code = main(
        [
            "review",
            "--provider",
            "paste",
            "--files",
            "src/app.py",
            "--question",
            "review",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.PROVIDER_FAILED
    assert captured.out == ""
    assert "provider failed" in captured.err
    assert "clean" in captured.err.lower()


def test_credentials_status_does_not_print_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """credentials status는 source 존재만 출력하고 key 값은 출력하지 않는다."""
    monkeypatch.setattr(
        "packet_ask.cli.credential_status",
        lambda provider: CredentialStatus(provider, "unset", "available", "keychain"),
    )
    code = main(["credentials", "status", "glm"])
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert (
        "glm | env=unset | keychain-item=available | auto-candidate=keychain"
        in captured.out
    )
    assert "secret" not in captured.out.lower()


def test_credentials_set_delegates_to_keychain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """credentials set은 선택한 provider를 Keychain 저장기로 넘긴다."""
    called: list[str] = []
    monkeypatch.setattr(
        "packet_ask.cli.store_macos_keychain",
        lambda provider, access: called.append(f"{provider}:{access}"),
    )
    code = main(
        [
            "credentials",
            "set",
            "glm",
            "--store",
            "macos-keychain",
            "--access",
            "command",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert called == ["glm:command"]
    assert "glm" in captured.out


def test_credentials_set_requires_explicit_access_mode() -> None:
    """Keychain 위협 모델은 안전·자동화 trade-off를 사용자가 직접 고른다."""
    with pytest.raises(SystemExit):
        main(["credentials", "set", "glm", "--store", "macos-keychain"])


def test_review_passes_explicit_credential_source_to_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """task의 credential source가 선택한 launch adapter까지 전달된다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    captured: dict[str, object] = {}

    def fake_launch(packet, timeout, credential_source):  # noqa: ANN001
        captured["source"] = credential_source
        captured["timeout"] = timeout
        return "reviewed"

    monkeypatch.setattr("packet_ask.cli.launch_glm", fake_launch)
    code = main(
        [
            "review",
            "--provider",
            "glm",
            "--credential-source",
            "keychain",
            "--files",
            "src/app.py",
            "--question",
            "리뷰해줘",
        ]
    )
    assert code == codes.SUCCESS
    assert captured["source"] == "keychain"
    assert captured["timeout"] == 1200


def test_dry_run_marks_timeout_informational_and_never_launches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """launch provider dry-run은 paste로 전환되어 deadline을 적용하지 않는다."""
    import json

    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)

    def fail_launch(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("dry-run must not launch a provider")

    monkeypatch.setattr("packet_ask.cli.launch_glm", fail_launch)
    code = main(
        [
            "review",
            "--provider",
            "glm",
            "--dry-run",
            "--json",
            "--files",
            "src/app.py",
            "--question",
            "review",
        ]
    )
    data = json.loads(capsys.readouterr().out)
    assert code == codes.SUCCESS
    assert data["receipt"]["provider"] == "paste"
    assert data["receipt"]["timeout_applies"] is False


def test_medium_packet_auto_timeout_reaches_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """>64KiB 최종 packet의 1500초 tier가 adapter까지 전달된다."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text("x" * 70_000, encoding="utf-8")
    monkeypatch.chdir(repo)
    captured: dict[str, int] = {}

    def fake_launch(packet, timeout, credential_source):  # noqa: ANN001
        captured["timeout"] = timeout
        return "reviewed"

    monkeypatch.setattr("packet_ask.cli.launch_glm", fake_launch)
    code = main(
        [
            "review",
            "--provider",
            "glm",
            "--files",
            "src/app.py",
            "--max-bytes",
            "100000",
            "--question",
            "review",
        ]
    )
    assert code == codes.SUCCESS
    assert captured["timeout"] == 1500


def test_explicit_timeout_reaches_launch_without_clamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """명시 timeout은 adapter 경계에서도 정확히 유지된다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    captured: dict[str, int] = {}

    def fake_launch(packet, timeout, credential_source):  # noqa: ANN001
        captured["timeout"] = timeout
        return "reviewed"

    monkeypatch.setattr("packet_ask.cli.launch_glm", fake_launch)
    code = main(
        [
            "review",
            "--provider",
            "glm",
            "--timeout",
            "300",
            "--files",
            "src/app.py",
            "--question",
            "review",
        ]
    )
    assert code == codes.SUCCESS
    assert captured["timeout"] == 300


@pytest.mark.parametrize(
    ("provider", "launcher_name"),
    [
        ("glm", "launch_glm"),
        ("kimi", "launch_kimi"),
        ("claude", "launch_claude"),
    ],
)
def test_builtin_registry_dispatches_each_launch_adapter(
    provider: str,
    launcher_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """registry가 고른 현재 CLI launcher에 timeout과 credential source를 전달한다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    called: list[tuple[int, str]] = []

    def fake_launch(_packet, timeout: int, credential_source: str) -> str:  # noqa: ANN001
        called.append((timeout, credential_source))
        return "reviewed"

    monkeypatch.setattr(cli, launcher_name, fake_launch)
    code = main(
        [
            "review",
            "--provider",
            provider,
            "--credential-source",
            "prompt",
            "--timeout",
            "321",
            "--files",
            "src/app.py",
            "--question",
            "review",
        ]
    )
    assert code == codes.SUCCESS
    assert called == [(321, "prompt")]


def test_user_alias_cannot_reach_builtin_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """user alias는 registry id가 없고 packet payload만 반환한다."""
    providers_file = tmp_path / "providers.toml"
    providers_file.write_text(
        'version = 1\n[providers.gemini]\nlabel = "Gemini"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PACKET_ASK_PROVIDERS_FILE", str(providers_file))

    def fail_launch(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("user alias must remain paste-only")

    monkeypatch.setattr(cli, "launch_glm", fail_launch)
    monkeypatch.setattr(cli, "launch_kimi", fail_launch)
    monkeypatch.setattr(cli, "launch_claude", fail_launch)
    packet = build_packet("review", "review", [], None, tmp_path / "packets")
    try:
        assert "# Task" in cli._execute_provider("gemini", packet, 1, "auto")
    finally:
        packet.destroy()
