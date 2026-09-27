"""권한 검사기의 후보 비실행과 정확한 커밋 결속을 검증한다."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def guard():
    spec = importlib.util.spec_from_file_location("authority_guard", ROOT / ".github/scripts/authority_guard.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL, text=True).strip()


@pytest.fixture
def candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "repo"
    (root / ".github").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / ".github/authority.toml").write_bytes((ROOT / ".github/authority.toml").read_bytes())
    (root / "src/app.py").write_text("VALUE = 1\n")
    (root / "tests/test_app.py").write_text("def test_value():\n    assert True\n")
    (root / ".gitignore").write_text(".exitzero/\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Authority Test")
    git(root, "config", "user.email", "authority@example.invalid")
    git(root, "add", ".")
    git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "baseline")
    return root, git(root, "rev-parse", "HEAD")


def commit(root: Path) -> str:
    git(root, "add", "-A")
    git(root, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "candidate")
    return git(root, "rev-parse", "HEAD")


def test_materialization_and_inspection_do_not_execute_candidate(guard, candidate, tmp_path):
    source, baseline = candidate
    marker = tmp_path / "must-not-execute"
    marker.write_text("positive path control")
    marker.unlink()
    (source / "src/app.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n")
    head = commit(source)
    target = tmp_path / "materialized"
    guard.copy_local_git(source, target, baseline, head)
    result = guard.inspect_candidate(target, baseline, head, Path(sys.executable))
    assert result["normal"]["exit_code"] == 0
    assert result["normal"]["permissions"]["base_commit"] == baseline
    assert result["normal"]["permissions"]["changes"][0]["zone"] == "editable"
    assert "src/app.py" in result["normal"]["inputs"]
    assert result["normal"] == json.loads((target / result["normal"]["receipt"]).read_text())
    assert result["reviewed"]["permissions"]["base_commit"] == head
    assert result["base_policy"] == result["candidate_policy"] == result["normal"]["policy_sha256"]
    assert not marker.exists()
    assert git(target, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("path,zone", [("tests/test_app.py", "protected"), (".github/authority.toml", "immutable")])
def test_zone_failure_remains_failure_before_approval(guard, candidate, tmp_path, path, zone):
    source, baseline = candidate
    changed = source / path
    changed.write_text(changed.read_text() + "\n# reviewed change candidate\n")
    head = commit(source)
    target = tmp_path / "candidate"
    guard.copy_local_git(source, target, baseline, head)
    result = guard.inspect_candidate(target, baseline, head, Path(sys.executable))
    assert result["normal"]["exit_code"] == 1
    assert {item["zone"] for item in result["normal"]["permissions"]["changes"]} == {zone}
    assert result["reviewed"]["exit_code"] == 0
    assert result["normal"]["checks"] == []


def test_governance_approval_cannot_introduce_executable_policy(guard, candidate, tmp_path):
    source, baseline = candidate
    policy = source / ".github/authority.toml"
    policy.write_text(policy.read_text().replace('kind = "python.syntax"', 'kind = "command"'))
    head = commit(source)
    target = tmp_path / "candidate"
    guard.copy_local_git(source, target, baseline, head)
    result = guard.inspect_candidate(target, baseline, head, Path(sys.executable))
    assert result["normal"]["exit_code"] == 1
    assert result["reviewed"] is None
    assert result["reviewed_error"] == "unsafe-authority-policy"


def test_symlink_is_rejected_before_reading_its_target(guard, candidate, tmp_path):
    source, baseline = candidate
    (source / "src/link.py").symlink_to(tmp_path / "outside")
    head = commit(source)
    with pytest.raises(guard.GuardError, match="regular"):
        guard.copy_local_git(source, tmp_path / "candidate", baseline, head)
    assert not (tmp_path / "outside").exists()


def test_sensitive_path_is_rejected_before_materialization(guard, candidate, tmp_path):
    source, baseline = candidate
    (source / ".env").write_text("SYNTHETIC_CONTROL=unused\n")
    head = commit(source)
    with pytest.raises(guard.GuardError, match="path"):
        guard.copy_local_git(source, tmp_path / "candidate", baseline, head)
    assert not (tmp_path / "candidate/.env").exists()


def test_approval_binding_changes_for_every_authority_coordinate(guard):
    binding = {"repository_id": 1349064041, "pr": 1, "base": "a" * 40,
               "head": "b" * 40, "merge_tree": "c" * 40, "base_policy": "d" * 64,
               "candidate_policy": "e" * 64, "mode": "protected"}
    original = guard.binding_id(binding)
    for name in binding:
        changed = dict(binding)
        changed[name] = 2 if isinstance(changed[name], int) else str(changed[name]) + "changed"
        assert guard.binding_id(changed) != original


def test_approval_requires_exact_successful_app_record(guard):
    expected = "binding"
    good = {"app": {"id": 123}, "external_id": expected, "name": guard.APPROVAL_NAME,
            "status": "completed", "conclusion": "success", "head_sha": "c" * 40}
    assert guard.approval_matches(good, 123, expected, "c" * 40)
    for update in ({"app": {"id": 15368}}, {"external_id": "old"}, {"conclusion": "neutral"},
                   {"status": "in_progress"}, {"head_sha": "b" * 40}):
        assert not guard.approval_matches(dict(good, **update), 123, expected, "c" * 40)


def test_manual_approval_requires_fresh_owner_dispatch(guard):
    guard.validate_dispatch(10, 10, 10, 1, "refs/heads/main")
    for args in ((10, 11, 10, 1, "refs/heads/main"), (10, 10, 11, 1, "refs/heads/main"),
                 (10, 10, 10, 2, "refs/heads/main"), (10, 10, 10, 1, "refs/heads/topic")):
        with pytest.raises(guard.GuardError, match="dispatch"):
            guard.validate_dispatch(*args)


def test_fetch_verifies_merge_ref_instead_of_first_fetch_head(guard, candidate, tmp_path, monkeypatch):
    source, baseline = candidate
    (source / "src/app.py").write_text("VALUE = 2\n")
    head = commit(source)
    tree = git(source, "rev-parse", head + "^{tree}")
    merged = git(source, "commit-tree", tree, "-p", baseline, "-p", head, "-m", "merge fixture")
    git(source, "update-ref", "refs/pull/1/merge", merged)
    original_git = guard.git

    def local_transport(root, *args, **kwargs):
        args = tuple(str(source) if arg == "https://github.com/ictechgy/packet-ask.git" else arg for arg in args)
        return original_git(root, *args, **kwargs)

    monkeypatch.setattr(guard, "git", local_transport)
    target = tmp_path / "fetched"
    guard.fetch_candidate(target, 1, baseline, merged, head, tree)
    assert git(target, "rev-parse", "HEAD") == merged
    assert (target / "src/app.py").read_text() == "VALUE = 2\n"
    with pytest.raises(guard.GuardError, match="merge-ref-changed"):
        guard.fetch_candidate(tmp_path / "stale", 1, baseline, baseline, head, tree)
    with pytest.raises(guard.GuardError, match="parents"):
        guard.fetch_candidate(tmp_path / "wrong-head", 1, baseline, merged, baseline, tree)
    with pytest.raises(guard.GuardError, match="tree"):
        guard.fetch_candidate(tmp_path / "wrong-tree", 1, baseline, merged, head, baseline)
