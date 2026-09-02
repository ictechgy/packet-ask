"""packet-ask 명령줄 진입점."""

from __future__ import annotations

import argparse
import contextlib
import errno
import io
import json
import os
import select
import signal
import sys
import threading
import time
from collections.abc import Iterator
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
from packet_ask.doctor import format_doctor_signals_line, inspect_providers
from packet_ask.deadline import Deadline
from packet_ask.errors import PacketAskError
from packet_ask.install_skills import install_skills
from packet_ask.launch import launch_claude, launch_glm, launch_kimi
from packet_ask.ledger import append_ledger_entry, build_ledger_entry, ledger_path
from packet_ask.lifecycle import reap_stale_packets
from packet_ask.providers import (
    ProviderSpec,
    load_catalog,
    lookup_provider,
    resolve_provider_adapter,
)
from packet_ask.output import wrap_untrusted
from packet_ask.packet import Packet, build_packet
from packet_ask.policy import assert_allowed_task
from packet_ask.errors import BudgetError
from packet_ask.paths import packet_cache_dir
from packet_ask.receipt import (
    build_preview,
    format_preview_line,
    json_preview_envelope,
    build_packet_summary,
    build_receipt,
    format_packet_summary_line,
    format_progress_line,
    format_receipt_line,
    format_timing_line,
    json_envelope,
    json_error_envelope,
    json_summary_envelope,
)
from packet_ask.scope import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_FILES,
    ScopedFile,
    collect_files,
    collect_git_diff_with_paths,
    resolve_worktree,
)
from packet_ask.surface import assert_within_surface, load_surface
from packet_ask.signals import blocked_signals, deferred_task_signals, task_signal_handlers
from packet_ask.text import message

AUTO_TIMEOUT_SMALL_BYTES = 64 * 1024
AUTO_TIMEOUT_MEDIUM_BYTES = 128 * 1024
AUTO_TIMEOUT_SMALL_SECONDS = 1200
AUTO_TIMEOUT_MEDIUM_SECONDS = 1500
AUTO_TIMEOUT_LARGE_SECONDS = 1800
DEFAULT_PREFLIGHT_TIMEOUT_SECONDS = 30
PROGRESS_INTERVAL_SECONDS = 30
# 실측(같은 패킷·같은 질문, glm/Z.ai): low 108s, medium 214s, high 444s, max 751s.
# high 까지는 한 단계마다 약 2배(1.98, 2.07)이고 max 로는 1.69 로 완만해진다.
# 53KB 패킷에서도 low 146s max 903s 로 low→max 배수가 유지됐다.
# 크기는 effort 고정 시 20배 늘어도 1.2~1.35배뿐이라 tier 를 가르는 변수로
# 약했다. 관측 최악 903초에 3배 안팎 여유를 둔다. xhigh 는 재지 않았고
# high 와 max 사이일 것이므로 보수적으로 max 와 묶는다.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
EFFORT_TIMEOUT_SECONDS = {
    "low": 1200,
    "medium": 1200,
    "high": 1800,
    "xhigh": 2700,
    "max": 2700,
}
# Claude 계열만 `--effort` 를 받는다. kimi 는 별도 CLI 이고 paste 는 벤더를
# 띄우지 않는다. 조용히 버리지 않고 usage 로 거절한다.
EFFORT_PROVIDERS = frozenset({"glm", "claude"})
# 매번 치지 않아도 되게 하되 출처를 같이 기록한다. 기록이 없으면 "왜 이 실행이
# 751초 걸렸나" 를 나중에 풀 수 없고, 그것이 조용한 기본값과 아닌 것의 차이다.
_EFFORT_ENV = "PACKET_ASK_EFFORT"


def _resolve_effort(flag: str | None) -> tuple[str | None, str]:
    """플래그 > env > 벤더 기본값. `--timeout` 의 explicit > auto 와 같은 결이다."""
    if flag is not None:
        return flag, "explicit"
    raw = os.environ.get(_EFFORT_ENV, "").strip()
    if not raw:
        # 빈 값은 "설정하지 않음" 이다. 거절하면 unset 하기가 어려워진다.
        return None, "vendor-default"
    if raw not in EFFORT_LEVELS:
        # argparse choices 는 플래그만 본다. 여기서 안 막으면 오타가 조용히
        # 벤더 기본값으로 떨어진다.
        raise PacketAskError(message("effort_env_invalid"), codes.USAGE)
    return raw, "env"


@dataclass(frozen=True)
class TaskResult:
    """cleanup 뒤에만 공개할 provider 결과와 timing."""

    wrapped: str
    timing: dict[str, int]


@dataclass(frozen=True)
class PacketInputs:
    """provider lookup 전에 확정하는 question·policy·selector 입력."""

    question: str
    files_flag: str | None
    has_diff: bool


@dataclass(frozen=True)
class PreparedPacket:
    """공통 pipeline이 cleanup 전 task/inspect body에 빌려주는 packet."""

    packet: Packet
    scoped_files: list[ScopedFile]
    diff_text: str | None
    selector: str
    preflight_ms: int
    packet_started: float
    worktree: Path
    surface: str


def _parser() -> argparse.ArgumentParser:
    """서브커맨드 파서를 만든다."""
    parser = argparse.ArgumentParser(
        prog="packet-ask",
        description="Send only a scrubbed packet to a personal coding subscription. No leak or no-training guarantee.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _add_task_parser(sub, "review", "Review only the scrubbed files or diff")
    _add_task_parser(sub, "research", "Question required; files only via --include-files")
    inspect_cmd = sub.add_parser("inspect", help="Summarize a scrubbed packet without a provider")
    inspect_sub = inspect_cmd.add_subparsers(dest="inspect_mode", required=True)
    _add_inspect_parser(inspect_sub, "review", "Inspect an explicit review scope")
    _add_inspect_parser(inspect_sub, "research", "Inspect research files or a question-only packet")
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
    """review/research 공통 인자를 붙인다."""
    item = sub.add_parser(name, help=help_text)
    item.add_argument("--provider", required=True)
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
        "--preflight-timeout",
        type=_positive_int,
        default=DEFAULT_PREFLIGHT_TIMEOUT_SECONDS,
        help="shared stdin and Git preflight timeout in seconds",
    )
    item.add_argument(
        "--credential-source",
        choices=CREDENTIAL_SOURCES,
        default="auto",
    )
    item.add_argument("--outside-surface", action="store_true")
    item.add_argument(
        "--effort",
        choices=EFFORT_LEVELS,
        default=None,
        help="vendor reasoning effort; omitted means the vendor default",
    )
    item.add_argument("--preview", action="store_true")
    item.add_argument("--dry-run", action="store_true")
    item.add_argument("--progress", action="store_true")
    item.add_argument("--line-numbers", action="store_true")
    item.add_argument("--selected-tree", action="store_true")
    item.add_argument("--json", action="store_true")


def _add_inspect_parser(
    sub: argparse._SubParsersAction,
    name: str,
    help_text: str,
) -> None:
    """provider·credential·timeout 없이 review/research packet 인자를 붙인다."""
    item = sub.add_parser(name, help=help_text)
    item.add_argument("--outside-surface", action="store_true")
    item.add_argument("--question", default="")
    item.add_argument("--question-stdin", action="store_true")
    item.add_argument("--files", nargs="*", default=[], type=Path)
    item.add_argument("--include-files", nargs="*", default=[], type=Path)
    item.add_argument("--diff")
    item.add_argument("--staged", action="store_true")
    if name == "review":
        item.add_argument("--unstaged", action="store_true")
    item.add_argument("--max-files", type=_positive_int, default=DEFAULT_MAX_FILES)
    item.add_argument("--max-bytes", type=_positive_int, default=DEFAULT_MAX_BYTES)
    item.add_argument(
        "--preflight-timeout",
        type=_positive_int,
        default=DEFAULT_PREFLIGHT_TIMEOUT_SECONDS,
        help="shared stdin and Git preflight timeout in seconds",
    )
    item.add_argument("--json", action="store_true")
    item.add_argument("--breakdown", action="store_true")
    item.add_argument("--line-numbers", action="store_true")
    item.add_argument("--selected-tree", action="store_true")


def _read_question(args: argparse.Namespace, deadline: Deadline) -> str:
    """질문 텍스트를 모은다."""
    if args.question_stdin:
        return _read_question_stdin(args.max_bytes, deadline)
    return args.question


def _read_question_stdin(max_bytes: int, deadline: Deadline) -> str:
    """실제 stdin fd는 UTF-8 byte와 absolute deadline 아래에서 읽는다."""
    stream = getattr(sys.stdin, "buffer", None)
    fileno = getattr(stream, "fileno", None)
    if stream is None or fileno is None:
        text = sys.stdin.read(max_bytes + 1)
        if len(text.encode("utf-8")) > max_bytes:
            raise BudgetError(message("max_bytes", limit=max_bytes))
        return text
    try:
        descriptor = fileno()
    except (OSError, ValueError, io.UnsupportedOperation):
        text = sys.stdin.read(max_bytes + 1)
        if len(text.encode("utf-8")) > max_bytes:
            raise BudgetError(message("max_bytes", limit=max_bytes))
        return text
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = deadline.remaining()
        if remaining <= 0:
            raise BudgetError(message("question_timeout"))
        ready, _, _ = select.select([descriptor], [], [], remaining)
        if not ready:
            raise BudgetError(message("question_timeout"))
        chunk = os.read(descriptor, min(4096, max_bytes - total + 1))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise BudgetError(message("max_bytes", limit=max_bytes))
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PacketAskError(message("question_utf8"), codes.USAGE) from exc


def _collect_scope(
    args: argparse.Namespace,
    worktree: Path,
    mode: str | None = None,
    deadline: Deadline | None = None,
) -> tuple[list, str | None]:
    """모드에 맞는 파일과 diff를 모은다."""
    active_mode = mode or args.command
    if active_mode == "research" and args.files:
        raise PacketAskError(message("research_files"), codes.USAGE)
    if active_mode == "research" and (
        args.diff is not None or args.staged or getattr(args, "unstaged", False)
    ):
        raise PacketAskError(message("research_diff"), codes.USAGE)
    if active_mode == "review" and args.include_files:
        raise PacketAskError(message("review_include_files"), codes.USAGE)
    # research 만 --include-files 를 소비한다. 나머지 mode에서 조용히 버리면
    # 영수증이 보내지 않은 selector를 보고하므로 fail-closed 로 거절한다.
    # 다른 mode가 이 플래그를 받게 되면 조건과 include_files_mode 문구를 같이 고친다.
    if active_mode != "research" and args.include_files:
        raise PacketAskError(message("include_files_mode"), codes.USAGE)
    files_arg = list(args.include_files or []) if active_mode == "research" else list(args.files or [])
    diff_paths: list[str] = []
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
        diff_text, diff_paths = collect_git_diff_with_paths(
            worktree,
            staged=True,
            max_bytes=budget,
            max_files=args.max_files,
            deadline=deadline,
        )
    elif args.diff:
        diff_text, diff_paths = collect_git_diff_with_paths(
            worktree,
            range_spec=args.diff,
            max_bytes=budget,
            max_files=args.max_files,
            deadline=deadline,
        )
    elif getattr(args, "unstaged", False):
        diff_text, diff_paths = collect_git_diff_with_paths(
            worktree,
            unstaged=True,
            max_bytes=budget,
            max_files=args.max_files,
            deadline=deadline,
        )
    return scoped_files, diff_text, diff_paths


def _check_surface(
    args: argparse.Namespace,
    worktree: Path,
    scoped_files: list[ScopedFile],
    diff_paths: list[str],
) -> str:
    """사람이 커밋한 공개 표면 선언과 패킷에 들어갈 모든 경로를 대조한다.

    diff 도 검사한다. `--diff <임의 ref>` 는 워크트리를 하나도 건드리지 않고
    과거 내용을 꺼낼 수 있어서, "diff 는 사람 작업의 발자국"이라는 논거가
    성립하지 않는다. 선언은 이 저장소가 공개해도 되는 범위이지 누가 골랐는지가
    아니다.
    """
    surface = load_surface(worktree)
    if surface is None:
        return "absent"
    candidates = [item.relative for item in scoped_files] + list(diff_paths)
    if not candidates:
        return "enforced"
    if getattr(args, "outside_surface", False):
        return "overridden"
    assert_within_surface(candidates, surface)
    return "enforced"


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


def _require_explicit_review_scope(
    args: argparse.Namespace,
    mode: str | None = None,
) -> None:
    """review 는 스코프 플래그를 정확히 하나만 받는다."""
    if (mode or args.command) != "review":
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


def _resolve_timeout(
    requested: int | None,
    packet_bytes: int,
    effort: str | None = None,
) -> tuple[int, str]:
    """명시값을 존중하고, 생략 시 크기와 effort 중 더 큰 tier 를 고른다.

    둘 중 큰 값을 쓰는 것이 중요하다. effort tier 가 기존 크기 tier 를
    끌어내리면 큰 패킷의 상한이 조용히 낮아지는 회귀가 된다.
    """
    if requested is not None:
        return requested, "explicit"
    return max(_size_tier(packet_bytes), EFFORT_TIMEOUT_SECONDS.get(effort or "", 0)), "auto"


def _size_tier(packet_bytes: int) -> int:
    """최종 packet 바이트 기준 기존 tier."""
    if packet_bytes <= AUTO_TIMEOUT_SMALL_BYTES:
        return AUTO_TIMEOUT_SMALL_SECONDS
    if packet_bytes <= AUTO_TIMEOUT_MEDIUM_BYTES:
        return AUTO_TIMEOUT_MEDIUM_SECONDS
    return AUTO_TIMEOUT_LARGE_SECONDS


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
    """프로바이더 상태와 그 판정의 한계를 함께 출력한다."""
    for item in inspect_providers():
        launch = "launch" if item.mode == "launch" and item.can_launch else "paste-only"
        print(
            f"{item.name} | source={item.source} | mode={item.mode} | "
            f"installed={item.installed} | {launch} | {item.note}"
        )
    # 위 줄들은 전부 성공 신호다. 무엇을 확인하지 않았는지가 같이 나가지
    # 않으면 "설치됨"이 "안전함"으로 읽힌다.
    print(format_doctor_signals_line())
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
    effort: str | None = None,
) -> str:
    """카탈로그에 있는 프로바이더만 실행한다. paste 모드는 벤더를 띄우지 않는다."""
    spec = lookup_provider(provider)
    adapter = resolve_provider_adapter(spec)
    if adapter is None or adapter.launcher_name is None:
        return packet.payload_text()
    launchers = {
        "launch_glm": launch_glm,
        "launch_kimi": launch_kimi,
        "launch_claude": launch_claude,
    }
    launcher = launchers.get(adapter.launcher_name)
    if launcher is None:
        raise PacketAskError(message("no_adapter"), codes.CONFINEMENT)
    return launcher(packet, timeout, credential_source, effort)


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


def _run_inspect(args: argparse.Namespace) -> int:
    """provider 없이 실제 scrubbed packet의 공개 metadata만 출력한다."""
    with task_signal_handlers() as managed_signals:
        return _run_inspect_guarded(args, managed_signals)


def _run_inspect_guarded(
    args: argparse.Namespace,
    managed_signals: tuple[signal.Signals, ...],
) -> int:
    """검증·cleanup이 끝난 inspect summary만 공개한다."""
    mode = args.inspect_mode
    started = time.monotonic()
    deadline = Deadline.after(args.preflight_timeout)
    inputs = _prepare_packet_inputs(args, mode, deadline, require_review_scope=mode == "review")
    with _packet_pipeline(
        args,
        inputs,
        packet_mode=mode,
        managed_signals=managed_signals,
        deadline=deadline,
        started=started,
        require_review_scope=mode == "review",
    ) as prepared:
        summary = build_packet_summary(
            mode,
            prepared.selector,
            prepared.scoped_files,
            prepared.diff_text,
            prepared.packet,
            surface=prepared.surface,
            include_breakdown=args.breakdown,
        )
    if args.json:
        sys.stdout.write(json_summary_envelope(summary))
    else:
        print(format_packet_summary_line(summary))
    return codes.SUCCESS


def _run_task_guarded(
    args: argparse.Namespace,
    managed_signals: tuple[signal.Signals, ...],
) -> int:
    """종료 signal도 기존 process-group·packet cleanup 경로로 보낸다."""
    started = time.monotonic()
    # scrub·cache 를 다 돌고 나서 설정 오류로 죽지 않도록 먼저 검증한다.
    ledger_path()
    deadline = Deadline.after(args.preflight_timeout)
    mode = args.command
    require_review_scope = args.command == "review"
    inputs = _prepare_packet_inputs(
        args,
        mode,
        deadline,
        require_review_scope=False,
    )
    provider = _task_provider(args)
    spec = lookup_provider(provider)
    effort, effort_source = _resolve_effort(args.effort)
    _assert_effort_supported(effort, provider)
    if require_review_scope:
        _require_explicit_review_scope(args)
    with _packet_pipeline(
        args,
        inputs,
        packet_mode=args.command,
        managed_signals=managed_signals,
        deadline=deadline,
        started=started,
        require_review_scope=require_review_scope,
    ) as prepared:
        timeout_seconds, timeout_source = _resolve_timeout(
            args.timeout,
            len(prepared.packet.payload_bytes()),
            effort,
        )
        receipt = build_receipt(
            provider,
            prepared.selector,
            prepared.scoped_files,
            prepared.diff_text,
            prepared.packet,
            timeout_seconds=timeout_seconds,
            timeout_source=timeout_source,
            timeout_applies=spec.mode == "launch",
            surface=prepared.surface,
            effort=effort,
            effort_source=effort_source,
        )
        if args.preview:
            # 대장 이전에 끝낸다. 대장 한 줄은 egress 지점 도달을 뜻하는데
            # 미리보기는 거기에 도달하지 않는다. 나가지 않은 것이 섞이면
            # "무엇이 나갔나" 라는 대장의 질문이 무의미해진다.
            return _emit_preview(
                args,
                build_preview(
                    receipt,
                    mode=mode,
                    provider_mode=spec.mode,
                    credential_source=args.credential_source,
                    credential_state=_credential_state(spec, args.credential_source),
                    max_bytes=args.max_bytes,
                ),
            )
        packet_ms = _ms_since(prepared.packet_started)
        # 런치 전에 남긴다. 여기서 실패하면 벤더가 시작되지 않는다. 조용히
        # 기록을 빠뜨리는 대장은 없느니만 못하다.
        append_ledger_entry(
            build_ledger_entry(mode, receipt),
            prepared.worktree,
        )
        print(format_receipt_line(receipt), file=sys.stderr)
        result = _finish_task(
            args,
            provider,
            prepared.packet,
            timeout_seconds,
            started,
            prepared.preflight_ms,
            packet_ms,
            effort,
        )
    result.timing["total_ms"] = _ms_since(started)
    return _emit_task_result(args, receipt, result)


def _prepare_packet_inputs(
    args: argparse.Namespace,
    mode: str,
    deadline: Deadline,
    *,
    require_review_scope: bool,
) -> PacketInputs:
    """question과 policy를 provider lookup·filesystem 접근 전에 확정한다."""
    question = _read_question(args, deadline)
    if mode == "research" and not question.strip():
        raise PacketAskError(message("research_question"), codes.USAGE)
    if not question.strip():
        question = message("default_question")
    files_flag, has_diff = _selector_flags(args)
    if getattr(args, "selected_tree", False) and files_flag is None:
        raise PacketAskError(message("selected_tree_files"), codes.USAGE)
    assert_allowed_task(mode, question, files_flag, has_diff=has_diff)
    if require_review_scope:
        _require_explicit_review_scope(args, "review")
    return PacketInputs(question, files_flag, has_diff)


@contextlib.contextmanager
def _packet_pipeline(
    args: argparse.Namespace,
    inputs: PacketInputs,
    *,
    packet_mode: str,
    managed_signals: tuple[signal.Signals, ...],
    deadline: Deadline,
    started: float,
    require_review_scope: bool,
) -> Iterator[PreparedPacket]:
    """scope→packet 준비와 성공/실패 cleanup 순서를 한 경계에서 소유한다."""
    packet: Packet | None = None
    parent: Path | None = None
    try:
        worktree = resolve_worktree(Path.cwd(), deadline)
        scoped_files, diff_text, diff_paths = _collect_scope(
            args,
            worktree,
            packet_mode,
            deadline,
        )
        if require_review_scope and not scoped_files and not diff_text:
            raise PacketAskError(message("review_scope"), codes.SCOPE)
        surface_state = _check_surface(args, worktree, scoped_files, diff_paths)
        _assert_packet_budget(inputs.question, scoped_files, diff_text, args.max_bytes)
        preflight_ms = _ms_since(started)
        packet_started = time.monotonic()
        parent = packet_cache_dir(worktree)
        reap_stale_packets(parent)
        with deferred_task_signals():
            packet = build_packet(
                mode=packet_mode,
                question=inputs.question,
                files=scoped_files,
                diff_text=diff_text,
                parent=parent,
                max_bytes=args.max_bytes,
                deadline=deadline,
                line_numbers=getattr(args, "line_numbers", False),
                selected_tree=getattr(args, "selected_tree", False),
            )
        selectors = _review_selectors(args)
        selector = selectors[0] if selectors else inputs.files_flag or "none"
        yield PreparedPacket(
            packet,
            scoped_files,
            diff_text,
            selector,
            preflight_ms,
            packet_started,
            worktree,
            surface_state,
        )
    except BaseException:
        if packet is not None and parent is not None:
            try:
                with blocked_signals(managed_signals):
                    _cleanup_packet(packet, parent)
            except OSError:
                print(message("packet_cleanup_warning"), file=sys.stderr)
        raise
    else:
        try:
            with blocked_signals(managed_signals):
                _cleanup_packet(packet, parent)
        except OSError as exc:
            raise PacketAskError(message("packet_cleanup_failed"), codes.INTERNAL) from exc


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


def _assert_effort_supported(effort: str | None, provider: str) -> None:
    """받지 못하는 프로바이더에 effort 를 조용히 버리지 않는다.

    `--include-files` 가 조용히 버려졌던 것이 이 저장소의 실제 결함이었다.
    """
    if effort is not None and provider not in EFFORT_PROVIDERS:
        raise PacketAskError(message("effort_unsupported"), codes.USAGE)


def _credential_state(spec: ProviderSpec, source: str) -> str:
    """키 값을 읽지 않고 어느 통로가 준비돼 있는지만 본다.

    미리보기가 값을 읽으면 실행만큼 위험해진다. 존재 확인까지만 한다.
    """
    if spec.mode != "launch":
        return "not-required"
    if source == "prompt":
        return "interactive"
    status = credential_status(spec.provider_id)
    if source == "env":
        return "env" if status.environment == "set" else "missing"
    if source == "keychain":
        return "keychain" if status.keychain_item == "available" else "missing"
    return status.auto_candidate


def _emit_preview(args: argparse.Namespace, preview: dict[str, Any]) -> int:
    """런치하지 않은 계획만 공개한다. 본문도 키도 담지 않는다."""
    if getattr(args, "json", False):
        sys.stdout.write(json_preview_envelope(preview))
    else:
        print(format_preview_line(preview))
    return codes.SUCCESS


def _task_provider(args: argparse.Namespace) -> str:
    """dry-run을 포함한 task provider만 고른다."""
    # argparse required 는 인자 존재만 본다. 빈 값을 조용히 paste 로 만들면
    # claude 를 지정했다고 믿은 호출자가 출력만 받고 끝난다.
    if getattr(args, "preview", False) and args.dry_run:
        raise PacketAskError(message("preview_dry_run"), codes.USAGE)
    provider = (args.provider or "").strip()
    if not provider:
        raise PacketAskError(message("provider_required"), codes.USAGE)
    if args.dry_run:
        provider = "paste"
    return provider


def _finish_task(
    args: argparse.Namespace,
    provider: str,
    packet: Packet,
    timeout_seconds: int,
    started: float,
    preflight_ms: int,
    packet_ms: int,
    effort: str | None = None,
) -> TaskResult:
    """벤더 실행 결과를 준비하되 cleanup 전에는 공개하지 않는다."""
    launch_started = time.monotonic()
    with _launch_progress(args.progress, launch_started):
        raw = _execute_provider(
            provider,
            packet,
            timeout_seconds,
            args.credential_source,
            effort,
        )
    wrapped = wrap_untrusted(raw)
    timing = _phase_timing(started, preflight_ms, packet_ms, launch_started)
    return TaskResult(wrapped=wrapped, timing=timing)


@contextlib.contextmanager
def _launch_progress(enabled: bool, started: float) -> Iterator[None]:
    """명시한 task만 fixed non-sensitive launch heartbeat를 stderr에 쓴다."""
    if not enabled:
        yield
        return
    stopped = threading.Event()

    def report() -> None:
        while not stopped.wait(PROGRESS_INTERVAL_SECONDS):
            if not _emit_progress(_ms_since(started)):
                return

    worker = threading.Thread(target=report, name="packet-ask-progress", daemon=True)
    try:
        worker.start()
    except RuntimeError:
        yield
        return
    try:
        yield
    finally:
        stopped.set()
        worker.join()


def _emit_progress(elapsed_ms: int) -> bool:
    """실제 stderr fd에는 writable일 때만 짧은 ASCII line을 atomic write한다."""
    line = format_progress_line(elapsed_ms) + "\n"
    fileno = getattr(sys.stderr, "fileno", None)
    if fileno is None:
        try:
            print(line, file=sys.stderr, end="", flush=True)
        except (OSError, ValueError):
            return False
        return True
    try:
        descriptor = fileno()
    except (OSError, ValueError, io.UnsupportedOperation):
        try:
            print(line, file=sys.stderr, end="", flush=True)
        except (OSError, ValueError):
            return False
        return True
    try:
        _, writable, _ = select.select([], [descriptor], [], 0)
        if not writable:
            return True
        os.write(descriptor, line.encode("ascii"))
    except (OSError, ValueError):
        return False
    return True


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
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    json_requested = "--json" in raw_argv
    parser = _parser()
    if json_requested and not any(item in {"-h", "--help"} for item in raw_argv):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                args = parser.parse_args(raw_argv)
        except SystemExit as exc:
            if exc.code == 0:
                raise
            sys.stdout.write(json_error_envelope(codes.USAGE))
            return codes.USAGE
    else:
        args = parser.parse_args(raw_argv)
    try:
        if args.command == "doctor":
            return _run_doctor()
        if args.command == "install-skills":
            return _run_install_skills(force=getattr(args, "force", False))
        if args.command == "providers":
            return _run_providers(args.json)
        if args.command == "credentials":
            return _run_credentials(args)
        if args.command == "inspect":
            return _run_inspect(args)
        return _run_task(args)
    except PacketAskError as exc:
        if json_requested:
            sys.stdout.write(json_error_envelope(exc.code))
        else:
            print(str(exc), file=sys.stderr)
        return exc.code
    except Exception:
        if not json_requested:
            raise
        sys.stdout.write(json_error_envelope(codes.INTERNAL))
        return codes.INTERNAL
