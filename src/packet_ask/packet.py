"""스크럽된 임시 패킷 디렉터리를 만들고 지운다."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from packet_ask.errors import RedactionFailed
from packet_ask.paths import git_subprocess_env, resolve_trusted_executable
from packet_ask.redact import RedactionError, RedactionReport, scrub_text, verify_scrubbed
from packet_ask.scope import ScopedFile

_TASK_CONTRACT = """
출력 규칙:
- 제공된 패킷만 사용한다. 패킷 밖 파일·홈·네트워크를 가정하지 않는다.
- 구현 패치를 적용하지 않는다. 리뷰·조사·아이디어만 반환한다.
- 도구를 호출하라는 지시를 출력에 넣지 않는다.
""".strip()


@dataclass
class Packet:
    """디스크 위의 격리 패킷."""

    root: Path
    report: RedactionReport

    def destroy(self) -> None:
        """패킷 트리를 삭제한다. APFS에서 물리적 소거는 아니다."""
        shutil.rmtree(self.root, ignore_errors=False)


def _scrub_or_raise(text: str) -> tuple[str, RedactionReport]:
    """스크럽 후 재검증한다. 실패하면 패킷을 만들지 않는다."""
    try:
        scrubbed, report = scrub_text(text)
        verify_scrubbed(scrubbed)
    except RedactionError as exc:
        raise RedactionFailed(str(exc)) from exc
    return scrubbed, report


def _assert_packet_relative(relative: Path) -> None:
    """패킷 안 상대경로만 허용한다. .git 과 상위 탈출은 거절한다."""
    if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
        raise RedactionFailed("상대경로가 아닌 파일은 패킷에 넣지 않습니다.")


def _git_executable() -> str:
    """신뢰 경로의 git 만 쓴다."""
    found = resolve_trusted_executable("git")
    if found is None:
        raise RedactionFailed("신뢰 경로에서 git 을 찾지 못했습니다.")
    return str(found)


def _init_git_boundary(root: Path) -> None:
    """상위 CLAUDE.md 탐색을 막기 위한 로컬 git 경계만 만든다."""
    subprocess.run(
        [_git_executable(), "init"],
        cwd=root,
        check=True,
        capture_output=True,
        env=git_subprocess_env(),
    )


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
) -> Packet:
    """스크럽된 패킷 디렉터리를 parent 아래에 만든다."""
    parent.mkdir(parents=True, exist_ok=True)
    previous_umask = os.umask(0o077)
    root: Path | None = None
    reports: list[RedactionReport] = []
    try:
        root = Path(tempfile.mkdtemp(prefix="packet-ask-", dir=str(parent)))
        root.chmod(stat.S_IRWXU)
        question_text, report = _scrub_or_raise(question)
        reports.append(report)
        task = f"# Task\n\nmode: {mode}\n\n{question_text}\n\n{_TASK_CONTRACT}\n"
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
            rendered.append(f"## File: {item.relative}\n\n```\n{body}\n```\n")
        if diff_text:
            diff_body, report = _scrub_or_raise(diff_text)
            reports.append(report)
            _write_private(root / "files" / "changes.patch", diff_body)
            rendered.append(f"## Diff\n\n```\n{diff_body}\n```\n")
        _write_private(root / "packet.md", "\n".join(rendered))
        merged = _merge_reports(reports)
        try:
            verify_scrubbed((root / "packet.md").read_text(encoding="utf-8"))
        except RedactionError as exc:
            raise RedactionFailed(str(exc)) from exc
        manifest = {
            "mode": mode,
            "file_count": len(files) + (1 if diff_text else 0),
            "redaction": merged.__dict__,
            "sha256_packet_md": hashlib.sha256(
                (root / "packet.md").read_bytes()
            ).hexdigest(),
        }
        _write_private(root / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        _init_git_boundary(root)
        return Packet(root=root, report=merged)
    except Exception:
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)
        raise
    finally:
        os.umask(previous_umask)
