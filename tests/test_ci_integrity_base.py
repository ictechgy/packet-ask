"""CI 기준점은 현재 후보가 아니라 검토 전 커밋을 가리켜야 한다."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/integrity_base.py"
REF = "refs/exitzero/test-integrity-base"


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
    )
    return completed.stdout.strip()


@pytest.fixture
def history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, str]:
    """원본 main과 그 뒤의 후보 커밋을 실제 Git 저장소로 만든다."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "CI Test")
    git(root, "config", "user.email", "ci@example.invalid")
    (root / "sample.txt").write_text("baseline\n")
    git(root, "add", ".")
    git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "baseline")
    baseline = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/remotes/origin/main", baseline)
    (root / "sample.txt").write_text("candidate\n")
    git(root, "add", ".")
    git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "candidate")
    return root, baseline, git(root, "rev-parse", "HEAD")


def prepare(root: Path, *args: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GITHUB_", "EXITZERO_CI_"))}
    env.update(overrides)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=root, env=env,
        capture_output=True, text=True, timeout=30,
    )


def assert_ready(root: Path, result: subprocess.CompletedProcess[str], baseline: str,
                 head: str, selection: str) -> None:
    assert result.returncode == 0, result.stderr
    assert git(root, "rev-parse", REF) == baseline
    record = json.loads((root / ".exitzero/test-integrity-base.json").read_text())
    assert record == json.loads(result.stdout)
    assert record["status"] == "ready"
    assert record["base_sha"] == baseline
    assert record["head_sha"] == head
    assert record["selection"] == selection


def test_pull_request_uses_event_base_not_origin_tip(history: tuple[Path, str, str]) -> None:
    root, baseline, head = history
    git(root, "update-ref", "refs/remotes/origin/main", head)
    result = prepare(root, GITHUB_EVENT_NAME="pull_request", EXITZERO_CI_PR_BASE=baseline)
    assert_ready(root, result, baseline, head, "pull_request_base")


def test_main_push_uses_before_not_checked_out_head(history: tuple[Path, str, str]) -> None:
    root, baseline, head = history
    git(root, "update-ref", "refs/remotes/origin/main", head)
    result = prepare(root, GITHUB_EVENT_NAME="push", GITHUB_REF="refs/heads/main",
                     EXITZERO_CI_PUSH_BEFORE=baseline)
    assert_ready(root, result, baseline, head, "main_push_before")


def test_branch_push_uses_common_ancestor(history: tuple[Path, str, str]) -> None:
    root, baseline, head = history
    git(root, "checkout", "-q", "-b", "other-main", baseline)
    (root / "other.txt").write_text("unrelated main change\n")
    git(root, "add", ".")
    git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "new main")
    git(root, "update-ref", "refs/remotes/origin/main", git(root, "rev-parse", "HEAD"))
    git(root, "checkout", "-q", "main")
    result = prepare(root, GITHUB_EVENT_NAME="push", GITHUB_REF="refs/heads/feature",
                     EXITZERO_CI_PUSH_BEFORE=head)
    assert_ready(root, result, baseline, head, "branch_merge_base")


def test_local_explicit_baseline(history: tuple[Path, str, str]) -> None:
    root, baseline, head = history
    assert_ready(root, prepare(root, "--base", baseline), baseline, head, "local_explicit")


@pytest.mark.parametrize("event,ref,field,value", [
    ("pull_request", "", "EXITZERO_CI_PR_BASE", ""),
    ("pull_request", "", "EXITZERO_CI_PR_BASE", "HEAD"),
    ("push", "refs/heads/main", "EXITZERO_CI_PUSH_BEFORE", "0" * 40),
    ("push", "refs/heads/main", "EXITZERO_CI_PUSH_BEFORE", "f" * 40),
    ("workflow_dispatch", "refs/heads/main", "EXITZERO_CI_PR_BASE", ""),
])
def test_bad_event_invalidates_previous_pin(history: tuple[Path, str, str],
                                           event: str, ref: str, field: str, value: str) -> None:
    root, baseline, head = history
    assert_ready(root, prepare(root, "--base", baseline), baseline, head, "local_explicit")
    result = prepare(root, GITHUB_EVENT_NAME=event, GITHUB_REF=ref, **{field: value})
    assert result.returncode == 2
    assert "기준 커밋을 준비하지 못했습니다" in result.stderr
    assert result.stdout == ""
    assert not (root / ".exitzero/test-integrity-base.json").exists()
    assert subprocess.run(["git", "rev-parse", "--verify", REF], cwd=root,
                          capture_output=True).returncode != 0
    assert git(root, "rev-parse", "HEAD") == head


def test_symbolic_pin_does_not_rewrite_main(history: tuple[Path, str, str]) -> None:
    root, baseline, head = history
    git(root, "symbolic-ref", REF, "refs/heads/main")
    assert_ready(root, prepare(root, "--base", baseline), baseline, head, "local_explicit")
    assert git(root, "rev-parse", "refs/heads/main") == head
    assert subprocess.run(["git", "symbolic-ref", "-q", REF], cwd=root,
                          capture_output=True).returncode != 0


def test_artifact_symlink_is_not_followed(history: tuple[Path, str, str], tmp_path: Path) -> None:
    root, _, head = history
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "test-integrity-base.json"
    marker.write_text("synthetic outside marker\n")
    (root / ".exitzero").symlink_to(outside, target_is_directory=True)
    result = prepare(root)
    assert result.returncode == 2
    assert "기준 커밋을 준비하지 못했습니다" in result.stderr
    assert marker.read_text() == "synthetic outside marker\n"
    assert git(root, "rev-parse", "HEAD") == head
