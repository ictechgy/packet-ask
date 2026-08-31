"""provider를 실행하지 않는 packet inspect 계약."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path

import pytest

from packet_ask import cli, codes
from packet_ask.cli import main


def _init_repo(root: Path, body: str = "print(1)\n") -> Path:
    """inspect 테스트용 최소 git 저장소."""
    root.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=root, check=True)
    source = root / "src" / "app.py"
    source.parent.mkdir()
    source.write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "src/app.py"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    return root


def _fail(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("inspect must not touch a provider or credential")


def test_inspect_review_prints_summary_without_provider_or_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """human inspect는 provider 경계 없이 metadata 한 줄만 cleanup 뒤 공개한다."""
    repo = _init_repo(tmp_path / "repo")
    cache_parent = tmp_path / "cache" / "packet-ask"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_CACHE_DIR", str(cache_parent))
    monkeypatch.setenv("PACKET_ASK_GLM_KEY", "glm-secret-must-not-appear")
    monkeypatch.setattr(cli, "lookup_provider", _fail)
    monkeypatch.setattr(cli, "_execute_provider", _fail)
    monkeypatch.setattr("packet_ask.doctor.inspect_provider", _fail)
    monkeypatch.setattr("packet_ask.keysource.resolve_provider_key", _fail)

    code = main(
        [
            "inspect",
            "review",
            "--files",
            "src/app.py",
            "--question",
            "private-question-marker",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert captured.err == ""
    assert captured.out.startswith("packet-ask inspect mode=review selector=files")
    assert captured.out.count("\n") == 1
    assert "src/app.py" in captured.out
    assert "print(1)" not in captured.out
    assert "private-question-marker" not in captured.out
    assert "glm-secret-must-not-appear" not in captured.out
    leftovers = list(cache_parent.glob("packet-ask-*")) if cache_parent.exists() else []
    assert leftovers == []


def test_inspect_json_reports_public_redaction_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """JSON summary는 본문 대신 고정 metadata와 redaction count만 담는다."""
    repo = _init_repo(tmp_path / "repo", "owner@example.com\n")
    monkeypatch.chdir(repo)
    code = main(
        [
            "inspect",
            "review",
            "--files",
            "src/app.py",
            "--question",
            "review",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    assert captured.err == ""
    data = json.loads(captured.out)
    assert data["schema"] == "packet-ask.v1"
    assert data["ok"] is True
    assert set(data) == {"schema", "ok", "summary"}
    summary = data["summary"]
    assert set(summary) == {
        "mode",
        "selector",
        "paths",
        "file_count",
        "bytes",
        "redaction",
        "sha256_packet_md",
    }
    assert summary["mode"] == "review"
    assert summary["selector"] == "files"
    assert summary["paths"] == ["src/app.py"]
    assert summary["file_count"] == 1
    assert summary["redaction"]["emails"] == 1
    assert "owner@example.com" not in captured.out


def test_inspect_breakdown_reports_per_item_bytes_without_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """명시 breakdown은 scrubbed byte/count만 더하고 기존 본문·질문은 숨긴다."""
    repo = _init_repo(tmp_path / "repo", "owner@example.com\n")
    monkeypatch.chdir(repo)
    question = "private review question"
    code = main(
        [
            "inspect",
            "review",
            "--files",
            "src/app.py",
            "--question",
            question,
            "--breakdown",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == codes.SUCCESS
    summary = json.loads(captured.out)["summary"]
    breakdown = summary["breakdown"]
    assert set(breakdown) == {"question_bytes", "framing_bytes", "items"}
    assert breakdown["question_bytes"] == len(question.encode("utf-8"))
    assert len(breakdown["items"]) == 1
    item = breakdown["items"][0]
    assert item["path"] == "src/app.py"
    assert item["bytes"] > 0
    assert item["redaction"]["emails"] == 1
    assert summary["bytes"] == (
        breakdown["question_bytes"] + breakdown["framing_bytes"] + item["bytes"]
    )
    assert question not in captured.out
    assert "owner@example.com" not in captured.out


def test_inspect_research_reuses_include_files_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """research inspect는 질문과 --include-files만 허용한다."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    code = main(
        [
            "inspect",
            "research",
            "--include-files",
            "src/app.py",
            "--question",
            "research this pattern",
            "--json",
        ]
    )
    data = json.loads(capsys.readouterr().out)
    assert code == codes.SUCCESS
    assert data["summary"]["mode"] == "research"
    assert data["summary"]["selector"] == "include-files"

    code = main(
        [
            "inspect",
            "research",
            "--files",
            "src/app.py",
            "--question",
            "research this pattern",
            "--json",
        ]
    )
    error = json.loads(capsys.readouterr().out)
    assert code == codes.POLICY
    assert error["error"]["kind"] == "policy"


def test_inspect_review_requires_one_scope_and_rejects_provider_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """review scope와 provider 없는 inspect 문법을 parse/policy 양쪽에서 고정한다."""
    code = main(["inspect", "review", "--json"])
    assert code == codes.SCOPE
    assert json.loads(capsys.readouterr().out)["error"]["kind"] == "scope"

    code = main(["inspect", "review", "--provider", "glm", "--json"])
    captured = capsys.readouterr()
    assert code == codes.USAGE
    assert captured.err == ""
    assert json.loads(captured.out)["error"]["kind"] == "usage"


def test_inspect_cleanup_failure_emits_no_success_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """packet 삭제가 실패하면 inspect도 성공 metadata를 먼저 내보내지 않는다."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)

    def fail_cleanup(_packet: object) -> None:
        raise OSError("blocked")

    monkeypatch.setattr("packet_ask.packet.Packet.destroy", fail_cleanup)
    code = main(
        [
            "inspect",
            "review",
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


def test_inspect_signal_after_build_cleans_packet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """inspect build 반환 경계의 SIGTERM도 summary 없이 packet을 정리한다."""
    repo = _init_repo(tmp_path / "repo")
    cache_parent = tmp_path / "cache" / "packet-ask"
    real_build = cli.build_packet

    def build_then_signal(*args: object, **kwargs: object):  # noqa: ANN202
        packet = real_build(*args, **kwargs)
        os.kill(os.getpid(), signal.SIGTERM)
        return packet

    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_CACHE_DIR", str(cache_parent))
    monkeypatch.setattr(cli, "build_packet", build_then_signal)
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "inspect",
                "review",
                "--files",
                "src/app.py",
                "--question",
                "review",
            ]
        )
    assert exc.value.code == 143
    leftovers = list(cache_parent.glob("packet-ask-*")) if cache_parent.exists() else []
    assert leftovers == []
