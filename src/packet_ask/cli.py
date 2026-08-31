"""packet-ask 명령줄 진입점."""

from __future__ import annotations

import argparse
import errno
import json
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packet_ask import codes
from packet_ask.keysource import (
    CREDENTIAL_PROVIDERS,
    CREDENTIAL_SOURCES,
    credential_status,
    store_macos_keychain,
)
from packet_ask.doctor import inspect_providers
from packet_ask.errors import PacketAskError
from packet_ask.install_skills import install_skills
from packet_ask.launch import launch_claude, launch_glm, launch_kimi
from packet_ask.lifecycle import reap_stale_packets
from packet_ask.providers import lookup_provider, load_catalog
from packet_ask.output import wrap_untrusted
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
from packet_ask.signals import blocked_signals, deferred_task_signals, task_signal_handlers
from packet_ask.text import message

AUTO_TIMEOUT_SMALL_BYTES = 64 * 1024
AUTO_TIMEOUT_MEDIUM_BYTES = 128 * 1024
AUTO_TIMEOUT_SMALL_SECONDS = 1200
AUTO_TIMEOUT_MEDIUM_SECONDS = 1500
AUTO_TIMEOUT_LARGE_SECONDS = 1800


@dataclass(frozen=True)
class TaskResult:
    """cleanup 뒤에만 공개할 provider 결과와 timing."""

    wrapped: str
    timing: dict[str, int]


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
    credentials_cmd = sub.add_parser("credentials", help="Manage credential sources")
    credentials_sub = credentials_cmd.add_subparsers(
        dest="credentials_command",
        required=True,
    )
    status_cmd = credentials_sub.add_parser("status", help="Show credential availability")
    status_cmd.add_argument("provider", nargs="?", choices=CREDENTIAL_PROVIDERS)
    set_cmd = credentials_sub.add_parser("set", help="Store a provider credential")
    set_cmd.add_argument("provider", choices=CREDENTIAL_PROVIDERS)
    set_cmd.add_argument(
        "--store",
        choices=("macos-keychain",),
        default="macos-keychain",
    )
    set_cmd.add_argument(
        "--access",
        choices=("command", "prompt"),
        required=True,
        help="command supports agents; prompt requires macOS approval on every read",
    )
    return parser


def _positive_int(raw: str) -> int:
    """자원 제한 인자는 1 이상의 정수만 받는다."""
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


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
    item.add_argument(
        "--timeout",
        type=_positive_int,
        default=None,
        help="provider timeout in seconds; default is auto by final packet bytes",
    )
    item.add_argument("--max-files", type=_positive_int, default=DEFAULT_MAX_FILES)
    item.add_argument("--max-bytes", type=_positive_int, default=DEFAULT_MAX_BYTES)
    item.add_argument(
        "--credential-source",
        choices=CREDENTIAL_SOURCES,
        default="auto",
    )
    item.add_argument("--dry-run", action="store_true")
    item.add_argument("--json", action="store_true")


def _read_question(args: argparse.Namespace) -> str:
    """질문 텍스트를 모은다."""
    if args.question_stdin:
        parts: list[str] = []
        total = 0
        while True:
            chunk = sys.stdin.read(min(4096, args.max_bytes - total + 1))
            if not chunk:
                break
            parts.append(chunk)
            total += len(chunk.encode("utf-8"))
            if total > args.max_bytes:
                raise BudgetError(f"total packet exceeds {args.max_bytes} bytes")
        return "".join(parts)
    return args.question


def _collect_scope(args: argparse.Namespace, worktree: Path) -> tuple[list, str | None]:
    """모드에 맞는 파일과 diff를 모은다."""
    if args.command == "research" and args.files:
        raise PacketAskError(message("research_files"), codes.USAGE)
    if args.command == "review" and args.include_files:
        raise PacketAskError(message("review_include_files"), codes.USAGE)
    files_arg = list(args.include_files or []) if args.command == "research" else list(args.files or [])
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
        diff_text = collect_git_diff(
            worktree, staged=True, max_bytes=budget, max_files=args.max_files
        )
    elif args.diff:
        diff_text = collect_git_diff(
            worktree, range_spec=args.diff, max_bytes=budget, max_files=args.max_files
        )
    elif getattr(args, "unstaged", False):
        diff_text = collect_git_diff(
            worktree, unstaged=True, max_bytes=budget, max_files=args.max_files
        )
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


def _resolve_timeout(requested: int | None, packet_bytes: int) -> tuple[int, str]:
    """명시값을 존중하고, 생략 시 넉넉한 packet-size tier를 고른다."""
    if requested is not None:
        return requested, "explicit"
    if packet_bytes <= AUTO_TIMEOUT_SMALL_BYTES:
        return AUTO_TIMEOUT_SMALL_SECONDS, "auto"
    if packet_bytes <= AUTO_TIMEOUT_MEDIUM_BYTES:
        return AUTO_TIMEOUT_MEDIUM_SECONDS, "auto"
    return AUTO_TIMEOUT_LARGE_SECONDS, "auto"


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


def _run_credentials(args: argparse.Namespace) -> int:
    """키 값 없이 status를 보거나 `/usr/bin/security`에 저장을 위임한다."""
    if args.credentials_command == "status":
        providers = [args.provider] if args.provider else list(CREDENTIAL_PROVIDERS)
        for provider in providers:
            status = credential_status(provider)
            print(
                f"{status.provider} | env={status.environment} | "
                f"keychain-item={status.keychain_item} | "
                f"auto-candidate={status.auto_candidate}"
            )
        return codes.SUCCESS
    if args.credentials_command == "set":
        store_macos_keychain(args.provider, access=args.access)
        print(
            message(
                "credential_saved",
                provider=args.provider,
                access=args.access,
            )
        )
        return codes.SUCCESS
    raise PacketAskError(message("no_adapter"), codes.USAGE)


def _execute_provider(
    provider: str,
    packet: Packet,
    timeout: int,
    credential_source: str,
) -> str:
    """카탈로그에 있는 프로바이더만 실행한다. paste 모드는 벤더를 띄우지 않는다."""
    spec = lookup_provider(provider)
    if spec.mode == "paste":
        return packet.payload_text()
    if spec.provider_id == "glm":
        return launch_glm(packet, timeout, credential_source)
    if spec.provider_id == "kimi":
        return launch_kimi(packet, timeout, credential_source)
    if spec.provider_id == "claude":
        return launch_claude(packet, timeout, credential_source)
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
    with task_signal_handlers() as managed_signals:
        return _run_task_guarded(args, managed_signals)


def _run_task_guarded(
    args: argparse.Namespace,
    managed_signals: tuple[signal.Signals, ...],
) -> int:
    """종료 signal도 기존 process-group·packet cleanup 경로로 보낸다."""
    started = time.monotonic()
    packet: Packet | None = None
    parent: Path | None = None
    try:
        provider, question, files_flag, has_diff = _task_inputs(args)
        mode = args.command if args.command != "paste" else "review"
        assert_allowed_task(mode, question, files_flag, has_diff=has_diff)
        spec = lookup_provider(provider)
        _require_explicit_review_scope(args)
        worktree = resolve_worktree(Path.cwd())
        scoped_files, diff_text = _collect_scope(args, worktree)
        if args.command == "review" and not scoped_files and not diff_text:
            raise PacketAskError(message("review_scope"), codes.SCOPE)
        _assert_packet_budget(question, scoped_files, diff_text, args.max_bytes)
        preflight_ms = _ms_since(started)
        packet_started = time.monotonic()
        parent = packet_cache_dir(worktree)
        reap_stale_packets(parent)
        with deferred_task_signals():
            packet = build_packet(
                mode=args.command,
                question=question,
                files=scoped_files,
                diff_text=diff_text,
                parent=parent,
                max_bytes=args.max_bytes,
            )
        timeout_seconds, timeout_source = _resolve_timeout(
            args.timeout,
            len(packet.payload_bytes()),
        )
        selector = _review_selectors(args)[0] if _review_selectors(args) else files_flag or "none"
        receipt = build_receipt(
            provider,
            selector,
            scoped_files,
            diff_text,
            packet,
            timeout_seconds=timeout_seconds,
            timeout_source=timeout_source,
            timeout_applies=spec.mode == "launch",
        )
        packet_ms = _ms_since(packet_started)
        print(format_receipt_line(receipt), file=sys.stderr)
        result = _finish_task(
            args,
            provider,
            packet,
            timeout_seconds,
            started,
            preflight_ms,
            packet_ms,
        )
    except BaseException:
        if packet is not None and parent is not None:
            try:
                with blocked_signals(managed_signals):
                    _cleanup_packet(packet, parent)
            except OSError:
                print(message("packet_cleanup_warning"), file=sys.stderr)
        raise
    try:
        with blocked_signals(managed_signals):
            _cleanup_packet(packet, parent)
    except OSError as exc:
        raise PacketAskError(message("packet_cleanup_failed"), codes.INTERNAL) from exc
    result.timing["total_ms"] = _ms_since(started)
    return _emit_task_result(args, receipt, result)


def _cleanup_packet(packet: Packet, parent: Path) -> None:
    """packet은 반드시 지우고 공유 cache parent 경합은 정상으로 취급한다."""
    packet.destroy()
    try:
        parent.rmdir()
    except FileNotFoundError:
        return
    except OSError as exc:
        if exc.errno in {errno.ENOTEMPTY, errno.EEXIST}:
            return
        raise


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
    timeout_seconds: int,
    started: float,
    preflight_ms: int,
    packet_ms: int,
) -> TaskResult:
    """벤더 실행 결과를 준비하되 cleanup 전에는 공개하지 않는다."""
    launch_started = time.monotonic()
    raw = _execute_provider(
        provider,
        packet,
        timeout_seconds,
        args.credential_source,
    )
    wrapped = wrap_untrusted(raw)
    timing = _phase_timing(started, preflight_ms, packet_ms, launch_started)
    return TaskResult(wrapped=wrapped, timing=timing)


def _emit_task_result(
    args: argparse.Namespace,
    receipt: dict[str, Any],
    result: TaskResult,
) -> int:
    """cleanup이 성공한 task 결과만 stdout/stderr에 공개한다."""
    print(format_timing_line(result.timing), file=sys.stderr)
    if getattr(args, "json", False):
        sys.stdout.write(json_envelope(receipt, result.wrapped, result.timing))
    else:
        sys.stdout.write(result.wrapped)
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
        if args.command == "credentials":
            return _run_credentials(args)
        return _run_task(args)
    except PacketAskError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
