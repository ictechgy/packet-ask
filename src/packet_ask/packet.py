"""스크럽된 임시 패킷 디렉터리를 만들고 지운다."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from packet_ask.errors import BudgetError, RedactionFailed, ScopeError
from packet_ask.deadline import Deadline
from packet_ask.lifecycle import close_packet_lease, create_packet_lease, remove_packet_tree
from packet_ask.redact import (
    RedactionError,
    RedactionReport,
    public_redaction_counts,
    scrub_text,
    verify_scrubbed,
)
from packet_ask.scope import GIT_METADATA_OUTPUT_BYTES, ScopedFile, run_bounded_git
from packet_ask.text import language, message

_TASK_CONTRACT_EN = """
Output rules:
- Use only the provided packet. Do not assume access to files, home, or network outside it.
- Do not implement or apply patches. Return only review, research, or ideas.
- Do not put instructions to invoke tools in the output.
""".strip()
_TASK_CONTRACT_KO = """
출력 규칙:
- 제공된 패킷만 사용한다. 패킷 밖 파일·홈·네트워크를 가정하지 않는다.
- 구현 패치를 적용하지 않는다. 리뷰·조사·아이디어만 반환한다.
- 도구를 호출하라는 지시를 출력에 넣지 않는다.
""".strip()
_LINE_NUMBER_NOTE_EN = (
    "Packet-local line numbers (stable only for this packet digest):"
)
_LINE_NUMBER_NOTE_KO = "패킷 로컬 줄 번호(이 패킷 digest에서만 고정):"


def _task_contract() -> str:
    """프로바이더에 보내는 계약도 CLI 언어 선택을 따른다."""
    return _TASK_CONTRACT_KO if language() == "ko" else _TASK_CONTRACT_EN


def _line_number_note() -> str:
    """줄 번호의 packet-local 의미도 CLI 언어 선택을 따른다."""
    return _LINE_NUMBER_NOTE_KO if language() == "ko" else _LINE_NUMBER_NOTE_EN


@dataclass
class Packet:
    """디스크 위의 격리 패킷."""

    root: Path
    report: RedactionReport
    _packet_text: str | None = field(default=None, repr=False)
    _packet_bytes: bytes | None = field(default=None, repr=False)
    _packet_digest: str | None = field(default=None, repr=False)
    _lease_fd: int | None = field(default=None, repr=False)
    _question_bytes: int = field(default=0, repr=False)
    _items: tuple[PacketItem, ...] = field(default=(), repr=False)

    def destroy(self) -> None:
        """패킷 트리를 삭제한다. APFS에서 물리적 소거는 아니다."""
        try:
            remove_packet_tree(self.root, directory_fd=self._lease_fd)
        finally:
            close_packet_lease(self._lease_fd)
            self._lease_fd = None

    def payload_text(self) -> str:
        """렌더링 payload 문자열을 한 번만 읽어 재사용한다."""
        if self._packet_text is None:
            self._packet_text = (self.root / "packet.md").read_text(encoding="utf-8")
        return self._packet_text

    def payload_bytes(self) -> bytes:
        """렌더링 payload 바이트를 한 번만 읽어 재사용한다."""
        if self._packet_bytes is None:
            if self._packet_text is not None:
                self._packet_bytes = self._packet_text.encode("utf-8")
            else:
                self._packet_bytes = (self.root / "packet.md").read_bytes()
        return self._packet_bytes

    def payload_digest(self) -> str:
        """packet.md SHA-256을 한 번만 계산한다."""
        if self._packet_digest is None:
            self._packet_digest = hashlib.sha256(self.payload_bytes()).hexdigest()
        return self._packet_digest

    def inspection_breakdown(self) -> dict[str, object]:
        """inspect 전용 allowlisted per-item metadata와 framing byte를 만든다."""
        items = [
            {
                "path": item.path,
                "bytes": item.bytes,
                "lines": item.lines,
                "redaction": public_redaction_counts(item.report),
            }
            for item in self._items
        ]
        content_bytes = self._question_bytes + sum(item.bytes for item in self._items)
        return {
            "question_bytes": self._question_bytes,
            "framing_bytes": max(0, len(self.payload_bytes()) - content_bytes),
            "items": items,
        }


@dataclass(frozen=True)
class PacketItem:
    """packet body 한 항목의 private inspection source."""

    path: str
    bytes: int
    lines: int
    report: RedactionReport = field(repr=False)


def _scrub_or_raise(text: str) -> tuple[str, RedactionReport]:
    """스크럽 후 재검증한다. 실패하면 패킷을 만들지 않는다."""
    try:
        scrubbed, report = scrub_text(text)
        verify_scrubbed(scrubbed)
    except RedactionError as exc:
        raise RedactionFailed(str(exc)) from exc
    return scrubbed, report


def _logical_line_count(text: str) -> int:
    """LF로 구분한 payload 줄 수를 센다. 마지막 LF는 새 줄을 만들지 않는다."""
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _render_numbered_body(text: str) -> str:
    """LF 구조를 보존하며 각 payload 줄에 고정 폭 packet-local gutter를 붙인다."""
    count = _logical_line_count(text)
    if count == 0:
        return ""
    parts = text.split("\n")
    if text.endswith("\n"):
        parts.pop()
    width = len(str(count))
    rendered = "\n".join(
        f"{number:>{width}} | {line}" for number, line in enumerate(parts, 1)
    )
    return rendered + ("\n" if text.endswith("\n") else "")


def _assert_packet_relative(relative: Path) -> None:
    """패킷 안 상대경로만 허용한다. .git 과 상위 탈출은 거절한다."""
    if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
        raise RedactionFailed(message("packet_relative"))


def _init_git_boundary(root: Path, deadline: Deadline | None = None) -> None:
    """상위 CLAUDE.md 탐색을 막기 위한 로컬 git 경계만 만든다."""
    try:
        kwargs = {"deadline": deadline} if deadline is not None else {}
        run_bounded_git(
            root,
            ["init"],
            GIT_METADATA_OUTPUT_BYTES,
            **kwargs,
        )
    except (BudgetError, ScopeError) as exc:
        raise RedactionFailed(message("packet_git_failed")) from exc


def _merge_reports(parts: list[RedactionReport]) -> RedactionReport:
    """여러 스크럽 횟수를 합친다."""
    merged = RedactionReport()
    for part in parts:
        merged.private_key_blocks += part.private_key_blocks
        merged.secret_lines += part.secret_lines
        merged.secret_values += part.secret_values
        merged.home_paths += part.home_paths
        merged.emails += part.emails
        merged.phones += part.phones
    return merged


def _write_private(path: Path, content: str) -> None:
    """0600 파일로 기록한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def build_packet(
    mode: str,
    question: str,
    files: list[ScopedFile],
    diff_text: str | None,
    parent: Path,
    max_bytes: int | None = None,
    deadline: Deadline | None = None,
    line_numbers: bool = False,
) -> Packet:
    """스크럽된 패킷 디렉터리를 parent 아래에 만든다."""
    parent.mkdir(parents=True, exist_ok=True)
    previous_umask = os.umask(0o077)
    root: Path | None = None
    lease_fd: int | None = None
    reports: list[RedactionReport] = []
    items: list[PacketItem] = []
    try:
        root = Path(tempfile.mkdtemp(prefix="packet-ask-", dir=str(parent)))
        root.chmod(stat.S_IRWXU)
        lease_fd = create_packet_lease(root)
        question_text, report = _scrub_or_raise(question)
        reports.append(report)
        question_bytes = len(question_text.encode("utf-8"))
        task = f"# Task\n\nmode: {mode}\n\n{question_text}\n\n{_task_contract()}\n"
        _write_private(root / "TASK.md", task)
        _write_private(root / "CLAUDE.md", "")
        _write_private(root / "AGENTS.md", "")
        _write_private(root / "KIMI.md", "")
        rendered: list[str] = [task, ""]
        for item in files:
            body, report = _scrub_or_raise(item.content)
            reports.append(report)
            relative = Path(item.relative)
            _assert_packet_relative(relative)
            _write_private(root / "files" / relative, body)
            items.append(
                PacketItem(
                    item.relative,
                    len(body.encode("utf-8")),
                    _logical_line_count(body),
                    report,
                )
            )
            rendered_body = _render_numbered_body(body) if line_numbers else body
            note = f"{_line_number_note()}\n\n" if line_numbers else ""
            rendered.append(
                f"## File: {item.relative}\n\n{note}```\n{rendered_body}\n```\n"
            )
        if diff_text:
            diff_body, report = _scrub_or_raise(diff_text)
            reports.append(report)
            _write_private(root / "files" / "changes.patch", diff_body)
            items.append(
                PacketItem(
                    "changes.patch",
                    len(diff_body.encode("utf-8")),
                    _logical_line_count(diff_body),
                    report,
                )
            )
            rendered.append(f"## Diff\n\n```\n{diff_body}\n```\n")
        packet_text = "\n".join(rendered)
        packet_bytes = packet_text.encode("utf-8")
        packet_digest = hashlib.sha256(packet_bytes).hexdigest()
        if max_bytes is not None and len(packet_bytes) > max_bytes:
            raise BudgetError(f"total packet exceeds {max_bytes} bytes")
        _write_private(root / "packet.md", packet_text)
        merged = _merge_reports(reports)
        try:
            verify_scrubbed((root / "packet.md").read_text(encoding="utf-8"))
        except RedactionError as exc:
            raise RedactionFailed(str(exc)) from exc
        manifest = {
            "mode": mode,
            "file_count": len(files) + (1 if diff_text else 0),
            "redaction": public_redaction_counts(merged),
            "sha256_packet_md": packet_digest,
        }
        _write_private(root / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        _init_git_boundary(root, deadline)
        return Packet(
            root=root,
            report=merged,
            _packet_text=packet_text,
            _packet_bytes=packet_bytes,
            _packet_digest=packet_digest,
            _lease_fd=lease_fd,
            _question_bytes=question_bytes,
            _items=tuple(items),
        )
    except BaseException:
        if root is not None:
            try:
                remove_packet_tree(root, directory_fd=lease_fd)
            except OSError:
                pass
            finally:
                close_packet_lease(lease_fd)
        raise
    finally:
        os.umask(previous_umask)
