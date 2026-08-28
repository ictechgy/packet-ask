"""packet-ask 명령줄 진입점."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from packet_ask import codes
from packet_ask.doctor import inspect_providers
from packet_ask.errors import PacketAskError
from packet_ask.install_skills import install_skills
from packet_ask.launch import launch_claude, launch_glm, launch_kimi
from packet_ask.providers import lookup_provider, load_catalog
from packet_ask.output import guard_provider_output, wrap_untrusted
from packet_ask.packet import Packet, build_packet
from packet_ask.policy import assert_allowed_task
from packet_ask.errors import BudgetError
from packet_ask.paths import packet_cache_dir
from packet_ask.receipt import build_receipt, format_receipt_line, format_timing_line, json_envelope
from packet_ask.scope import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_FILES,
    ScopedFile,
    collect_files,
    collect_git_diff,
    resolve_worktree,
)
from packet_ask.text import message


def _parser() -> argparse.ArgumentParser:
    """서브커맨드 파서를 만든다."""
    parser = argparse.ArgumentParser(
        prog="packet-ask",
        description="Send only a scrubbed packet to a personal coding subscription. No leak or no-training guarantee.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _add_task_parser(sub, "review", "Review only the scrubbed files or diff")
    _add_task_parser(sub, "research", "Question required; files only via --include-files")
    _add_task_parser(sub, "brainstorm", "Brainstorm from a scrubbed question")
    _add_task_parser(sub, "paste", "Print a packet without launching a vendor")
    sub.add_parser("doctor", help="Check official CLI one-shot flags")
    providers_cmd = sub.add_parser("providers", help="List sub providers")
    providers_cmd.add_argument("--json", action="store_true")
    skills_cmd = sub.add_parser("install-skills", help="Install harness skills")
    skills_cmd.add_argument("--force", action="store_true")
    return parser


def _add_task_parser(sub: argparse._SubParsersAction, name: str, help_text: str) -> None:
    """review/research/brainstorm/paste 공통 인자를 붙인다."""
    item = sub.add_parser(name, help=help_text)
    item.add_argument("--provider", required=name != "paste")
    item.add_argument("--question", default="")
    item.add_argument("--question-stdin", action="store_true")
    item.add_argument("--files", nargs="*", default=[], type=Path)
    item.add_argument("--include-files", nargs="*", default=[], type=Path)
    item.add_argument("--diff")
    item.add_argument("--staged", action="store_true")
    if name == "review":
        item.add_argument("--unstaged", action="store_true")
    item.add_argument("--timeout", type=int, default=300)
    item.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    item.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    item.add_argument("--dry-run", action="store_true")
    item.add_argument("--json", action="store_true")


def _read_question(args: argparse.Namespace) -> str:
    """질문 텍스트를 모은다."""
    if args.question_stdin:
        return sys.stdin.read()
    return args.question


def _collect_scope(args: argparse.Namespace, worktree: Path) -> tuple[list, str | None]:
    """모드에 맞는 파일과 diff를 모은다."""
    files_arg = list(args.include_files or []) if args.command == "research" else list(args.files or [])
    if args.command == "research" and args.files:
        files_arg = []
        # policy가 files 플래그를 거절하게 한다.
    scoped_files = []
    if files_arg:
        scoped_files = collect_files(
            worktree,
            files_arg,
            max_files=args.max_files,
            max_bytes=args.max_bytes,
        )
    diff_text = None
    budget = args.max_bytes
    if args.staged:
        diff_text = collect_git_diff(worktree, staged=True, max_bytes=budget)
    elif args.diff:
        diff_text = collect_git_diff(worktree, range_spec=args.diff, max_bytes=budget)
    elif getattr(args, "unstaged", False):
        diff_text = collect_git_diff(worktree, unstaged=True, max_bytes=budget)
    return scoped_files, diff_text


def _selector_flags(args: argparse.Namespace) -> tuple[str | None, bool]:
    """파일 플래그 이름과 로컬 diff 여부를 본다."""
    files_flag = None
    if args.files:
        files_flag = "files"
    elif args.include_files:
        files_flag = "include-files"
    has_diff = bool(args.diff or args.staged or getattr(args, "unstaged", False))
    return files_flag, has_diff


def _review_selectors(args: argparse.Namespace) -> list[str]:
    """review 가 고른 스코프 플래그 이름."""
    names: list[str] = []
    if args.files:
        names.append("files")
    if args.diff:
        names.append("diff")
    if args.staged:
        names.append("staged")
    if getattr(args, "unstaged", False):
        names.append("unstaged")
    return names


def _require_explicit_review_scope(args: argparse.Namespace) -> None:
    """review 는 스코프 플래그를 정확히 하나만 받는다."""
    if args.command != "review":
        return
    if len(_review_selectors(args)) == 1:
        return
    raise PacketAskError(message("review_scope"), codes.SCOPE)


def _payload_bytes(question: str, files: list[ScopedFile], diff_text: str | None) -> int:
    """질문·파일·diff 를 합친 UTF-8 바이트."""
    total = len(question.encode("utf-8"))
    for item in files:
        total += len(item.content.encode("utf-8"))
    if diff_text:
        total += len(diff_text.encode("utf-8"))
    return total


def _assert_packet_budget(question: str, files: list[ScopedFile], diff_text: str | None, max_bytes: int) -> None:
    """패킷 전체가 max_bytes 를 넘으면 거절한다."""
    if _payload_bytes(question, files, diff_text) > max_bytes:
        raise BudgetError(f"total packet exceeds {max_bytes} bytes")


def _run_install_skills(force: bool) -> int:
    """Claude/Codex/Grok 홈에 스킬을 심는다."""
    for path in install_skills(force=force):
        print(path)
    return codes.SUCCESS


def _run_doctor() -> int:
    """프로바이더 상태를 출력한다."""
    for item in inspect_providers():
        launch = "launch" if item.mode == "launch" and item.can_launch else "paste-only"
        print(
            f"{item.name} | source={item.source} | mode={item.mode} | "
            f"installed={item.installed} | {launch} | {item.note}"
        )
    return codes.SUCCESS


def _run_providers(as_json: bool) -> int:
    """카탈로그를 출력한다. 비밀 값은 넣지 않는다."""
    if as_json:
        rows = [
            {
                "id": spec.provider_id,
                "label": spec.label,
                "source": spec.source,
                "mode": spec.mode,
                "binary": spec.binary,
                "key_env": spec.key_env,
                "note": spec.note,
            }
            for spec in load_catalog()
        ]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return codes.SUCCESS
    return _run_doctor()


def _execute_provider(provider: str, packet: Packet, timeout: int) -> str:
    """카탈로그에 있는 프로바이더만 실행한다. paste 모드는 벤더를 띄우지 않는다."""
    spec = lookup_provider(provider)
    if spec.mode == "paste":
        return (packet.root / "packet.md").read_text(encoding="utf-8")
    if spec.provider_id == "glm":
        return launch_glm(packet, timeout)
    if spec.provider_id == "kimi":
        return launch_kimi(packet, timeout)
    if spec.provider_id == "claude":
        return launch_claude(packet, timeout)
    raise PacketAskError(message("no_adapter"), codes.CONFINEMENT)


def _ms_since(started: float) -> int:
    """단조 시계 경과 밀리초. 비밀 값은 담지 않는다."""
    return max(0, int((time.monotonic() - started) * 1000))


def _phase_timing(
    started: float, preflight_ms: int, packet_ms: int, launch_started: float
) -> dict[str, int]:
    """성공 경로의 구간 시간."""
    return {
        "preflight_ms": preflight_ms,
        "packet_ms": packet_ms,
        "launch_ms": _ms_since(launch_started),
        "total_ms": _ms_since(started),
    }


def _run_task(args: argparse.Namespace) -> int:
    """패킷을 만들고 프로바이더 또는 paste로 보낸다."""
    started = time.monotonic()
    provider, question, files_flag, has_diff = _task_inputs(args)
    mode = args.command if args.command != "paste" else "review"
    assert_allowed_task(mode, question, files_flag, has_diff=has_diff)
    _require_explicit_review_scope(args)
    worktree = resolve_worktree(Path.cwd())
    scoped_files, diff_text = _collect_scope(args, worktree)
    if args.command == "review" and not scoped_files and not diff_text:
        raise PacketAskError(message("review_scope"), codes.SCOPE)
    _assert_packet_budget(question, scoped_files, diff_text, args.max_bytes)
    preflight_ms = _ms_since(started)
    packet_started = time.monotonic()
    parent = packet_cache_dir(worktree)
    packet = build_packet(
        mode=args.command,
        question=question,
        files=scoped_files,
        diff_text=diff_text,
        parent=parent,
    )
    selector = _review_selectors(args)[0] if _review_selectors(args) else files_flag or "none"
    receipt = build_receipt(provider, selector, scoped_files, diff_text, packet)
    packet_ms = _ms_since(packet_started)
    print(format_receipt_line(receipt), file=sys.stderr)
    try:
        return _finish_task(args, provider, packet, receipt, started, preflight_ms, packet_ms)
    finally:
        packet.destroy()
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()


def _task_inputs(args: argparse.Namespace) -> tuple[str, str, str | None, bool]:
    """프로바이더·질문·스코프 플래그를 모은다."""
    provider = "paste" if args.command == "paste" else (args.provider or "paste")
    if args.dry_run:
        provider = "paste"
    question = _read_question(args)
    if args.command == "research" and not question.strip():
        raise PacketAskError(message("research_question"), codes.USAGE)
    if not question.strip():
        question = message("default_question")
    files_flag, has_diff = _selector_flags(args)
    return provider, question, files_flag, has_diff


def _finish_task(
    args: argparse.Namespace,
    provider: str,
    packet: Packet,
    receipt: dict[str, Any],
    started: float,
    preflight_ms: int,
    packet_ms: int,
) -> int:
    """벤더 실행 후 타이밍과 출력을 쓴다. 패킷 삭제는 호출측 finally."""
    launch_started = time.monotonic()
    raw = _execute_provider(provider, packet, args.timeout)
    guard_provider_output(raw)
    wrapped = wrap_untrusted(raw)
    timing = _phase_timing(started, preflight_ms, packet_ms, launch_started)
    print(format_timing_line(timing), file=sys.stderr)
    if getattr(args, "json", False):
        sys.stdout.write(json_envelope(receipt, wrapped, timing))
    else:
        sys.stdout.write(wrapped)
    return codes.SUCCESS


def main(argv: list[str] | None = None) -> int:
    """CLI 메인. 예외는 종료 코드로 바꾼다."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return _run_doctor()
        if args.command == "install-skills":
            return _run_install_skills(force=getattr(args, "force", False))
        if args.command == "providers":
            return _run_providers(args.json)
        return _run_task(args)
    except PacketAskError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
