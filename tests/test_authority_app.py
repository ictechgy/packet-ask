"""App 발급 판정은 검증한 병합 커밋과 독립 승인에만 결속한다."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE, HEAD, MERGE = "a" * 40, "b" * 40, "c" * 40


@pytest.fixture
def app():
    spec = importlib.util.spec_from_file_location("authority_app", ROOT / ".github/scripts/authority_app.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Api:
    def __init__(self):
        self.pr = {"number": 1, "state": "open", "draft": False, "mergeable": True,
                   "base": {"ref": "main", "sha": BASE, "repo": {"id": 1349064041}},
                   "head": {"sha": HEAD}, "merge_commit_sha": MERGE}
        self.run = {"id": 50, "workflow_id": 20, "event": "pull_request", "head_sha": HEAD,
                    "pull_requests": [{"number": 1}], "status": "completed"}
        self.checks = []
        self.writes = []

    def __call__(self, method, path, body=None):
        if method == "GET":
            if path == "/repos/ictechgy/packet-ask":
                return {"id": 1349064041, "full_name": "ictechgy/packet-ask", "owner": {"id": 10}}
            if path.endswith("/actions/workflows/ci.yml"):
                return {"id": 20, "path": ".github/workflows/ci.yml"}
            if path.endswith("/actions/runs/50"):
                return copy.deepcopy(self.run)
            if path.endswith("/pulls/1"):
                return copy.deepcopy(self.pr)
            if "/check-runs?" in path:
                return {"total_count": len(self.checks), "check_runs": copy.deepcopy(self.checks)}
            raise AssertionError(path)
        self.writes.append((method, path, copy.deepcopy(body)))
        if method == "POST":
            created = dict(body, id=len(self.checks) + 1, app={"id": 123})
            self.checks.append(created)
            return copy.deepcopy(created)
        if method == "PATCH":
            target = next(c for c in self.checks if c["id"] == int(path.rsplit("/", 1)[1]))
            target.update(body)
            return copy.deepcopy(target)
        raise AssertionError(method)


def event():
    return {"repository": {"id": 1349064041, "full_name": "ictechgy/packet-ask"},
            "sender": {"id": 10}, "workflow_run": {"id": 50}}


def context(app, api):
    return app.resolve_context(api, "workflow_run", event(), actor=10, attempt=1,
                               ref="refs/heads/main", workflow_sha=BASE)


def analysis(app, api, zone="protected"):
    ctx = context(app, api)
    normal = {"exit_code": 1, "permissions": {"status": "failed", "changes": [
        {"path": "tests/test_sample.py", "zone": zone, "decision": "review_required"}]}}
    return {"context": ctx, "inspection": {"base_policy": "d" * 64, "candidate_policy": "e" * 64,
            "normal": normal, "reviewed": {"exit_code": 0, "permissions": {"status": "passed"}}}}


def test_run_head_is_pr_head_and_result_targets_merge_commit(app):
    api = Api()
    ctx = context(app, api)
    assert ctx["head"] == HEAD and ctx["merge"] == MERGE
    result = analysis(app, api)
    assert app.publish(result, api, api, 123) == "failure"
    assert api.checks[-1]["head_sha"] == MERGE
    assert api.checks[-1]["name"] == "permission-authority"


@pytest.mark.parametrize("field,value", [("head_sha", MERGE), ("workflow_id", 21), ("event", "push")])
def test_unrelated_workflow_run_is_not_authority(app, field, value):
    api = Api()
    api.run[field] = value
    with pytest.raises(app.guard.GuardError):
        context(app, api)
    assert api.writes == []


def test_stale_pr_does_not_publish_any_check(app):
    api = Api()
    result = analysis(app, api)
    api.pr["head"]["sha"] = "f" * 40
    with pytest.raises(app.guard.GuardError, match="changed"):
        app.publish(result, api, api, 123)
    assert api.writes == []


def test_actions_issuer_cannot_forge_approval(app):
    api = Api()
    result = analysis(app, api)
    fake = {"id": 90, "name": app.guard.APPROVAL_NAME, "app": {"id": 15368},
            "head_sha": MERGE, "status": "completed", "conclusion": "success",
            "external_id": app.guard.binding_id(app.binding(result, "protected"))}
    api.checks.append(fake)
    assert app.publish(result, api, api, 123) == "failure"


def test_owner_approval_and_auto_recheck_share_final_identity(app):
    api = Api()
    result = analysis(app, api)
    ctx = result["context"]
    ctx.update(mode="protected", requested_policy="e" * 64,
               dispatch={"owner": 10, "actor": 10, "sender": 10, "attempt": 1, "ref": "refs/heads/main"})
    assert app.publish(result, api, api, 123) == "success"
    final_id = next(c["id"] for c in api.checks if c["name"] == app.guard.CHECK_NAME)
    ctx["mode"] = "auto"
    assert app.publish(result, api, api, 123) == "success"
    finals = [c for c in api.checks if c["name"] == app.guard.CHECK_NAME]
    assert len(finals) == 1 and finals[0]["id"] == final_id


def test_protected_approval_cannot_waive_immutable_edit(app):
    api = Api()
    result = analysis(app, api, zone="immutable")
    result["context"].update(mode="protected", requested_policy="e" * 64,
        dispatch={"owner": 10, "actor": 10, "sender": 10, "attempt": 1, "ref": "refs/heads/main"})
    assert app.publish(result, api, api, 123) == "failure"
    assert all(c["name"] != app.guard.APPROVAL_NAME for c in api.checks)


def test_governance_requires_safe_reviewed_result(app):
    api = Api()
    result = analysis(app, api, zone="immutable")
    result["context"].update(mode="governance", requested_policy="e" * 64,
        dispatch={"owner": 10, "actor": 10, "sender": 10, "attempt": 1, "ref": "refs/heads/main"})
    result["inspection"]["reviewed"] = None
    assert app.publish(result, api, api, 123) == "failure"
    assert all(c["name"] != app.guard.APPROVAL_NAME for c in api.checks)


def test_dispatch_replay_rejected_before_app_use(app):
    api = Api()
    payload = event()
    payload["inputs"] = {"pr": "1", "mode": "protected", "head": HEAD, "base": BASE,
                         "merge": MERGE, "policy": "e" * 64}
    with pytest.raises(app.guard.GuardError, match="dispatch"):
        app.resolve_context(api, "workflow_dispatch", payload, actor=10, attempt=2,
                            ref="refs/heads/main", workflow_sha=BASE)
    assert api.writes == []


def test_governance_approval_can_authorize_safe_immutable_change(app):
    api = Api()
    result = analysis(app, api, zone="immutable")
    result["context"].update(mode="governance", requested_policy="e" * 64,
        dispatch={"owner": 10, "actor": 10, "sender": 10, "attempt": 1, "ref": "refs/heads/main"})
    assert app.publish(result, api, api, 123) == "success"
    assert {c["name"] for c in api.checks} == {app.guard.APPROVAL_NAME, app.guard.CHECK_NAME}


@pytest.mark.parametrize("coordinate", ["base", "head", "merge"])
def test_old_approval_does_not_authorize_changed_commits(app, coordinate):
    api = Api()
    result = analysis(app, api)
    result["context"].update(mode="protected", requested_policy="e" * 64,
        dispatch={"owner": 10, "actor": 10, "sender": 10, "attempt": 1, "ref": "refs/heads/main"})
    assert app.publish(result, api, api, 123) == "success"
    if coordinate == "merge":
        api.pr["merge_commit_sha"] = "f" * 40
    else:
        api.pr[coordinate]["sha"] = "f" * 40
    result["context"].update(mode="auto", **{coordinate: "f" * 40})
    assert app.publish(result, api, api, 123) == "failure"
    assert api.checks[-1]["conclusion"] == "failure"


@pytest.mark.parametrize("failure", ["policy", "error", "syntax"])
def test_owner_dispatch_cannot_approve_stale_policy_or_failed_inspection(app, failure):
    api = Api()
    result = analysis(app, api)
    result["context"].update(mode="protected", requested_policy="e" * 64,
        dispatch={"owner": 10, "actor": 10, "sender": 10, "attempt": 1, "ref": "refs/heads/main"})
    if failure == "policy":
        result["context"]["requested_policy"] = "f" * 64
        with pytest.raises(app.guard.GuardError, match="policy-changed"):
            app.publish(result, api, api, 123)
        assert api.writes == []
    else:
        result["inspection"]["reviewed"]["exit_code"] = 2 if failure == "error" else 1
        assert app.publish(result, api, api, 123) == "failure"
        assert all(c["name"] != app.guard.APPROVAL_NAME for c in api.checks)


def test_editable_change_passes_without_approval(app):
    api = Api()
    result = analysis(app, api)
    result["inspection"]["normal"] = {"exit_code": 0, "permissions": {"status": "passed"}}
    assert app.publish(result, api, api, 123) == "success"
    assert len(api.checks) == 1 and api.checks[0]["name"] == app.guard.CHECK_NAME


def test_cli_transport_preserves_the_required_pr_merge_sha_contract(app, monkeypatch):
    api = Api()

    def transport(command, **kwargs):
        method = command[command.index("--method") + 1]
        path = command[command.index("--method") + 2]
        result = api(method, path)
        # 실제 2026-03-10 응답은 이 필드를 제거한다. 현재 지원 버전의 응답 계약을 사용한다.
        if path.endswith("/pulls/1") and "X-GitHub-Api-Version: 2022-11-28" not in command:
            result.pop("merge_commit_sha")
        assert "synthetic-token" not in command
        assert kwargs["env"]["GH_TOKEN"] == "synthetic-token"
        return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

    monkeypatch.setattr(app.subprocess, "run", transport)
    ctx = context(app, app.github_api("synthetic-token"))
    assert ctx["base"] == BASE and ctx["head"] == HEAD and ctx["merge"] == MERGE
