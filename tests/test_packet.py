"""패킷 디렉터리 생성과 스크럽 반영."""

import subprocess
from pathlib import Path

import pytest

from packet_ask.errors import BudgetError, RedactionFailed, ScopeError
from packet_ask.packet import build_packet
from packet_ask.scope import ScopedFile


def test_packet_rewrites_home_and_has_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """패킷은 홈 경로를 지우고 git 경계를 만든다."""

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    files = [ScopedFile(relative="src/app.py", content=f"log {home}/secret.py\n")]
    packet = build_packet(
        mode="review",
        question="이 파일의 경쟁 상태를 찾아줘",
        files=files,
        diff_text=None,
        parent=tmp_path / "packets",
    )
    written = (packet.root / "files" / "src" / "app.py").read_text(encoding="utf-8")
    assert (packet.root / "CLAUDE.md").read_text(encoding="utf-8") == ""
    assert str(home) not in written
    assert "[REDACTED HOME]" in written
    assert (packet.root / "CLAUDE.md").is_file()
    assert (packet.root / "TASK.md").is_file()
    assert (packet.root / "packet.md").is_file()
    assert (packet.root / ".git").is_dir()
    packet.destroy()
    assert not packet.root.exists()


def test_packet_md_contains_task_and_files(tmp_path: Path) -> None:
    """packet.md 는 질문과 파일 본문을 담는다."""
    files = [ScopedFile(relative="a.py", content="x = 1\n")]
    packet = build_packet(
        mode="research",
        question="이 제안에 대한 외부 자료를 조사해줘",
        files=files,
        diff_text=None,
        parent=tmp_path,
    )
    blob = (packet.root / "packet.md").read_text(encoding="utf-8")
    assert "외부 자료" in blob
    assert "a.py" in blob
    assert "x = 1" in blob
    packet.destroy()


def test_packet_diff_does_not_retain_unicode_adjacent_email(tmp_path: Path) -> None:
    """diff 조각과 최종 packet 모두 Unicode 인접 이메일 원문을 담지 않는다."""
    sample = "+한alice@example.com\n"
    packet = build_packet(
        mode="review",
        question="review",
        files=[],
        diff_text=sample,
        parent=tmp_path,
    )
    diff = (packet.root / "files" / "changes.patch").read_text(encoding="utf-8")
    assert "@example.com" not in diff
    assert "@example.com" not in packet.payload_text()
    assert "[REDACTED EMAIL]" in diff
    assert packet.report.emails == 1
    packet.destroy()


def test_packet_rejects_obfuscated_international_email(tmp_path: Path) -> None:
    """부분 redaction할 수 없는 Unicode mailbox는 packet/provider 전에 fail-closed한다."""
    local = "".join(chr(item) for item in (0x7528, 0x6237))
    domain = "".join(chr(item) for item in (0x4F8B, 0x5B50, 0x3002, 0x516C, 0x53F8))
    with pytest.raises(RedactionFailed, match="email"):
        build_packet(
            mode="review",
            question="review",
            files=[],
            diff_text=f"+{local}@{domain}\n",
            parent=tmp_path,
        )
    assert list(tmp_path.glob("packet-ask-*")) == []


def test_packet_rejects_format_obfuscated_secret_family(tmp_path: Path) -> None:
    """Cf로 끊은 token family도 최종 packet/provider 전에 fail-closed한다."""
    source = "eyJ" + "A" * 8 + "." + "B" * 8 + "." + "C" * 8
    obfuscated = source[:2] + chr(0x200B) + source[2:]
    with pytest.raises(RedactionFailed, match="secret"):
        build_packet(
            mode="review",
            question="review",
            files=[],
            diff_text="+" + obfuscated + "\n",
            parent=tmp_path,
        )
    assert list(tmp_path.glob("packet-ask-*")) == []


def test_packet_contract_uses_selected_language(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """패킷 내부 계약도 기본 영어와 명시 한글을 구분한다."""
    monkeypatch.setenv("PACKET_ASK_LANG", "en")
    english = build_packet("review", "review", [], None, tmp_path / "en")
    assert "Output rules:" in (english.root / "packet.md").read_text(encoding="utf-8")
    english.destroy()

    monkeypatch.setenv("PACKET_ASK_LANG", "ko")
    korean = build_packet("review", "리뷰", [], None, tmp_path / "ko")
    assert "출력 규칙:" in (korean.root / "packet.md").read_text(encoding="utf-8")
    korean.destroy()


def test_packet_stores_payload_away_from_control_files(tmp_path: Path) -> None:
    """수집 파일은 files/ 아래에 두어 CLAUDE.md 와 겹치지 않게 한다."""
    files = [ScopedFile(relative="CLAUDE.md", content="# user file\n")]
    packet = build_packet(
        mode="review",
        question="이 파일을 리뷰해줘",
        files=files,
        diff_text=None,
        parent=tmp_path,
    )
    assert (packet.root / "CLAUDE.md").read_text(encoding="utf-8") == ""
    assert "# user file" in (packet.root / "files" / "CLAUDE.md").read_text(encoding="utf-8")
    packet.destroy()


def test_git_boundary_uses_bounded_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """패킷 git init도 공통 bounded runner와 metadata 상한을 쓴다."""
    captured: dict[str, object] = {}

    def bounded(worktree: Path, extra: list[str], max_bytes: int) -> str:
        captured["worktree"] = worktree
        captured["extra"] = extra
        captured["max_bytes"] = max_bytes
        (worktree / ".git").mkdir()
        return ""

    monkeypatch.setattr("packet_ask.packet.run_bounded_git", bounded)
    files = [ScopedFile(relative="a.py", content="x = 1\n")]
    packet = build_packet(
        mode="review",
        question="이 파일을 리뷰해줘",
        files=files,
        diff_text=None,
        parent=tmp_path,
    )
    assert captured["extra"] == ["init"]
    assert captured["max_bytes"] == 4096
    packet.destroy()


def test_packet_git_boundary_runner_does_not_copy_parent_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """공통 runner로 수렴해도 packet git init의 최소 env 계약을 유지한다."""
    captured: list[dict[str, str]] = []
    real_popen = subprocess.Popen

    def spy(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        command = [str(part) for part in args[0]] if args else []  # type: ignore[index]
        env = kwargs.get("env")
        if "init" in command and isinstance(env, dict):
            captured.append(env)
        return real_popen(*args, **kwargs)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-secret")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "outside.git"))
    monkeypatch.setattr("packet_ask.scope.subprocess.Popen", spy)
    packet = build_packet("review", "review", [], None, tmp_path / "packets")
    assert captured
    assert all("ANTHROPIC_API_KEY" not in env for env in captured)
    assert all("parent-secret" not in env.values() for env in captured)
    assert all("GIT_DIR" not in env for env in captured)
    packet.destroy()


def test_packet_rejects_git_relative_path(tmp_path: Path) -> None:
    """.git 상대경로는 패킷에 쓰지 않는다."""
    files = [ScopedFile(relative=".git/config", content="[core]\n")]
    with pytest.raises(RedactionFailed):
        build_packet(
            mode="review",
            question="이 설정을 리뷰해줘",
            files=files,
            diff_text=None,
            parent=tmp_path,
        )


def test_packet_git_init_timeout_is_redaction_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """packet-local git init의 timeout은 traceback 대신 안정된 오류가 된다."""
    monkeypatch.setattr(
        "packet_ask.packet.run_bounded_git",
        lambda *_args: (_ for _ in ()).throw(ScopeError("timeout")),
    )
    with pytest.raises(RedactionFailed):
        build_packet("review", "review", [], None, tmp_path / "packets")
    assert list((tmp_path / "packets").glob("packet-ask-*")) == []


def test_packet_git_init_nonzero_is_redaction_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git init 실패는 CalledProcessError를 외부로 노출하지 않는다."""
    monkeypatch.setattr(
        "packet_ask.packet.run_bounded_git",
        lambda *_args: (_ for _ in ()).throw(BudgetError("too large")),
    )
    with pytest.raises(RedactionFailed):
        build_packet("review", "review", [], None, tmp_path / "packets")


def test_built_packet_reuses_cached_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """receipt와 launch용 payload·digest를 packet.md에서 반복해 읽지 않는다."""
    packet = build_packet("review", "review", [], None, tmp_path / "packets")

    def fail_read(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("packet.md must use the in-memory payload")

    monkeypatch.setattr(Path, "read_text", fail_read)
    monkeypatch.setattr(Path, "read_bytes", fail_read)
    assert "# Task" in packet.payload_text()
    assert packet.payload_bytes().startswith(b"# Task")
    assert len(packet.payload_digest()) == 64
    packet.destroy()
