"""packet-ask 명령줄 진입점."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from packet_ask import codes
from packet_ask.doctor import inspect_providers
from packet_ask.errors import PacketAskError
from packet_ask.launch import launch_glm, launch_kimi
from packet_ask.output import wrap_untrusted
from packet_ask.packet import Packet, build_packet
from packet_ask.policy import assert_allowed_task
from packet_ask.scope import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_FILES,
    collect_files,
    collect_git_diff,
    resolve_worktree,
)


def _parser() -> argparse.ArgumentParser:
    """서브커맨드 파서를 만든다."""
    parser = argparse.ArgumentParser(
        prog="packet-ask",
        description="개인 코딩 구독에 패킷만 보냅니다. 유출 없음·학습 금지를 보장하지 않습니다.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _add_task_parser(sub, "review", "스크럽된 diff/파일만 리뷰 요청")
    _add_task_parser(sub, "research", "질문은 필수, 파일은 --include-files")
    _add_task_parser(sub, "brainstorm", "스크럽된 질문으로 브레인스토밍")
    _add_task_parser(sub, "paste", "벤더를 실행하지 않고 패킷만 출력")
    sub.add_parser("doctor", help="공식 CLI 격리 원샷 가능 여부")
    return parser


def _add_task_parser(sub: argparse._SubParsersAction, name: str, help_text: str) -> None:
    """review/research/brainstorm/paste 공통 인자를 붙인다."""
    item = sub.add_parser(name, help=help_text)
    item.add_argument("--provider", required=name != "paste", choices=("glm", "kimi", "paste"))
    item.add_argument("--question", default="")
    item.add_argument("--question-stdin", action="store_true")
    item.add_argument("--files", nargs="*", default=[], type=Path)
    item.add_argument("--include-files", nargs="*", default=[], type=Path)
    item.add_argument("--diff")
    item.add_argument("--staged", action="store_true")
    item.add_argument("--timeout", type=int, default=300)
    item.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    item.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    item.add_argument("--dry-run", action="store_true")


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
    if args.staged:
        diff_text = collect_git_diff(worktree, staged=True)
    elif args.diff:
        diff_text = collect_git_diff(worktree, range_spec=args.diff)
    elif args.command == "review" and not scoped_files:
        diff_text = collect_git_diff(worktree, unstaged=True)
    return scoped_files, diff_text


def _run_doctor() -> int:
    """프로바이더 상태를 출력한다."""
    for item in inspect_providers():
        launch = "launch" if item.can_launch else "paste-only"
        print(f"{item.name} | installed={item.installed} | {launch} | {item.note}")
    return codes.SUCCESS


def _execute_provider(provider: str, packet: Packet, timeout: int) -> str:
    """허용된 프로바이더만 실행한다."""
    if provider == "paste":
        return (packet.root / "packet.md").read_text(encoding="utf-8")
    if provider == "glm":
        return launch_glm(packet, timeout)
    if provider == "kimi":
        return launch_kimi(packet, timeout)
    raise PacketAskError("알 수 없는 프로바이더입니다.", codes.USAGE)


def _run_task(args: argparse.Namespace) -> int:
    """패킷을 만들고 프로바이더 또는 paste로 보낸다."""
    provider = "paste" if args.command == "paste" else args.provider
    if args.dry_run:
        provider = "paste"
    question = _read_question(args)
    if args.command == "research" and not question.strip():
        raise PacketAskError("research는 --question 이 필요합니다.", codes.USAGE)
    if not question.strip():
        question = "이 패킷을 검토하세요. 구현하지 마세요."
    files_flag = None
    if args.files:
        files_flag = "files"
    elif args.include_files:
        files_flag = "include-files"
    assert_allowed_task(args.command if args.command != "paste" else "review", question, files_flag)
    worktree = resolve_worktree(Path.cwd())
    scoped_files, diff_text = _collect_scope(args, worktree)
    if args.command == "review" and not scoped_files and not diff_text:
        raise PacketAskError("review는 --files, --diff, --staged 중 하나가 필요합니다.", codes.SCOPE)
    parent = Path.cwd() / ".packet-ask-tmp"
    packet = build_packet(
        mode=args.command,
        question=question,
        files=scoped_files,
        diff_text=diff_text,
        parent=parent,
    )
    try:
        raw = _execute_provider(provider, packet, args.timeout)
        sys.stdout.write(wrap_untrusted(raw))
        return codes.SUCCESS
    finally:
        packet.destroy()
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()


def main(argv: list[str] | None = None) -> int:
    """CLI 메인. 예외는 종료 코드로 바꾼다."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return _run_doctor()
        return _run_task(args)
    except PacketAskError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
