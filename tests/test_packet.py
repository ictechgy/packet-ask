"""패킷 디렉터리 생성과 스크럽 반영."""

from pathlib import Path

import pytest

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
    written = (packet.root / "src" / "app.py").read_text(encoding="utf-8")
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
