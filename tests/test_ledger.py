"""opt-in 발송 ledger. payload 는 절대 기록하지 않는다."""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask.cli import main
from packet_ask.errors import PacketAskError
from packet_ask.ledger import append_ledger_entry, ledger_path


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


def test_ledger_is_off_until_the_env_var_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """기본값은 꺼짐이다. --progress 와 같은 opt-in 규약을 따른다."""
    monkeypatch.delenv("PACKET_ASK_LEDGER", raising=False)
    assert ledger_path() is None


def test_ledger_path_must_be_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    """상대 경로는 cwd 에 따라 달라지므로 confinement 로 거절한다."""
    monkeypatch.setenv("PACKET_ASK_LEDGER", "notes/egress.jsonl")
    with pytest.raises(PacketAskError) as excinfo:
        ledger_path()
    assert excinfo.value.code == codes.CONFINEMENT


def test_ledger_rejects_a_path_inside_the_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """워크트리 안의 ledger 는 스스로 packet 범위에 들어가므로 거절한다."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.setenv("PACKET_ASK_LEDGER", str(repo / "egress.jsonl"))
    with pytest.raises(PacketAskError) as excinfo:
        append_ledger_entry({"provider": "paste"}, worktree=repo)
    assert excinfo.value.code == codes.CONFINEMENT


def test_ledger_appends_one_json_line_with_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """append-only JSONL 이고 파일 권한은 0600 이다."""
    target = tmp_path / "out" / "egress.jsonl"
    monkeypatch.setenv("PACKET_ASK_LEDGER", str(target))
    append_ledger_entry({"provider": "paste", "bytes": 10}, worktree=None)
    append_ledger_entry({"provider": "glm", "bytes": 20}, worktree=None)
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["provider"] == "paste"
    assert json.loads(lines[1])["provider"] == "glm"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_ledger_rejects_a_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """심링크 대상이 바뀌면 기록이 남의 파일로 새므로 거절한다."""
    real = tmp_path / "real.jsonl"
    real.write_text("", encoding="utf-8")
    link = tmp_path / "link.jsonl"
    link.symlink_to(real)
    monkeypatch.setenv("PACKET_ASK_LEDGER", str(link))
    with pytest.raises(PacketAskError) as excinfo:
        append_ledger_entry({"provider": "paste"}, worktree=None)
    assert excinfo.value.code == codes.CONFINEMENT


def test_task_run_records_scope_but_never_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실제 실행이 범위·digest 는 남기고 질문과 본문은 남기지 않는다."""
    repo = _init_repo(tmp_path / "repo")
    target = tmp_path / "egress.jsonl"
    monkeypatch.setenv("PACKET_ASK_LEDGER", str(target))
    monkeypatch.chdir(repo)
    secret_question = "이 변경에서 초민감한단어 를 리뷰해줘"
    assert main(
        ["review", "--provider", "paste", "--files", "src/app.py",
         "--question", secret_question]
    ) == codes.SUCCESS
    raw = target.read_text(encoding="utf-8")
    entry = json.loads(raw.strip())
    assert entry["provider"] == "paste"
    assert entry["mode"] == "review"
    assert entry["selector"] == "files"
    assert entry["paths"] == ["src/app.py"]
    assert entry["bytes"] > 0
    assert len(entry["sha256_packet_md"]) == 64
    assert entry["timestamp"].endswith("Z")
    # 질문도 파일 본문도 절대 남지 않는다.
    assert "초민감한단어" not in raw
    assert "print(1)" not in raw


def test_ledger_failure_blocks_the_vendor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기록할 수 없으면 실행하지 않는다. 조용히 기록을 빠뜨리는 ledger 는 없느니만 못하다."""
    repo = _init_repo(tmp_path / "repo")
    blocked = tmp_path / "nodir"
    blocked.mkdir()
    blocked.chmod(0o500)
    monkeypatch.setenv("PACKET_ASK_LEDGER", str(blocked / "sub" / "egress.jsonl"))
    monkeypatch.chdir(repo)
    try:
        code = main(
            ["review", "--provider", "paste", "--files", "src/app.py",
             "--question", "이 변경을 리뷰해줘"]
        )
    finally:
        blocked.chmod(0o700)
    assert code == codes.CONFINEMENT
