"""main의 검사기만 PR 객체를 읽고 독립 App 검사와 운영자 승인을 발급한다."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

_spec = importlib.util.spec_from_file_location("authority_guard", Path(__file__).with_name("authority_guard.py"))
if _spec is None or _spec.loader is None:
    raise RuntimeError("authority-guard-unavailable")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

REPOSITORY = "ictechgy/packet-ask"
REPOSITORY_ID = 1349064041
API_ROOT = "/repos/" + REPOSITORY


def positive_id(value) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,12}", str(value)) or int(value) < 1:
        raise guard.GuardError("invalid-identifier")
    return int(value)


def digest(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise guard.GuardError("invalid-policy-digest")
    return value


def github_api(token: str):
    if not token or "\n" in token or "\r" in token:
        raise guard.GuardError("missing-api-token")

    def call(method: str, path: str, body: dict | None = None) -> dict:
        if not path.startswith(API_ROOT + "/") and path != API_ROOT:
            raise guard.GuardError("unexpected-api-path")
        env = guard.safe_environment()
        env.update(GH_TOKEN=token, GH_HOST="github.com", GH_PROMPT_DISABLED="1")
        command = ["gh", "api", "--hostname", "github.com", "--method", method, path,
                   "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28"]
        if body is not None:
            command += ["--input", "-"]
        result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                                env=env, capture_output=True, text=True, timeout=45)
        if result.returncode or len(result.stdout) > 16 * 1024 * 1024:
            raise guard.GuardError("github-api-failed")
        return json.loads(result.stdout)
    return call


def pr_context(api, number: int) -> dict:
    pr = api("GET", API_ROOT + f"/pulls/{positive_id(number)}")
    if (pr["number"] != number or pr["state"] != "open" or pr["draft"]
            or pr["mergeable"] is not True or pr["base"]["ref"] != "main"
            or pr["base"]["repo"]["id"] != REPOSITORY_ID):
        raise guard.GuardError("ineligible-pull-request")
    return {"repository_id": REPOSITORY_ID, "pr": number, "base": guard.sha(pr["base"]["sha"]),
            "head": guard.sha(pr["head"]["sha"]), "merge": guard.sha(pr["merge_commit_sha"])}


def resolve_context(api, event_name: str, event: dict, *, actor: int, attempt: int,
                    ref: str, workflow_sha: str) -> dict:
    repo = api("GET", API_ROOT)
    if any(r["id"] != REPOSITORY_ID or r["full_name"] != REPOSITORY for r in (repo, event["repository"])):
        raise guard.GuardError("unexpected-repository")
    if ref != "refs/heads/main":
        raise guard.GuardError("untrusted-workflow-ref")
    if event_name == "workflow_run":
        run_id = positive_id(event["workflow_run"]["id"])
        run = api("GET", API_ROOT + f"/actions/runs/{run_id}")
        workflow = api("GET", API_ROOT + "/actions/workflows/ci.yml")
        if (run["id"] != run_id or run["workflow_id"] != workflow["id"]
                or workflow["path"] != ".github/workflows/ci.yml" or run["event"] != "pull_request"
                or run["status"] != "completed" or len(run["pull_requests"]) != 1):
            raise guard.GuardError("unrelated-workflow-run")
        ctx = pr_context(api, positive_id(run["pull_requests"][0]["number"]))
        if run["head_sha"] != ctx["head"]:
            raise guard.GuardError("workflow-head-changed")
        ctx["mode"] = "auto"
    elif event_name == "workflow_dispatch":
        dispatch = {"owner": positive_id(repo["owner"]["id"]), "actor": actor,
                    "sender": positive_id(event["sender"]["id"]), "attempt": attempt, "ref": ref}
        guard.validate_dispatch(**dispatch)
        inputs = event["inputs"]
        if inputs["mode"] not in {"check", "protected", "governance"}:
            raise guard.GuardError("invalid-dispatch-mode")
        ctx = pr_context(api, positive_id(inputs["pr"]))
        for coordinate in ("base", "head", "merge"):
            if guard.sha(inputs[coordinate]) != ctx[coordinate]:
                raise guard.GuardError("dispatch-tuple-changed")
        ctx.update(mode=inputs["mode"], requested_policy=digest(inputs["policy"]), dispatch=dispatch)
    else:
        raise guard.GuardError("unsupported-event")
    # 오래된 main 검사기의 실행도 최신 권한으로 간주하지 않는다.
    if guard.sha(workflow_sha) != ctx["base"]:
        raise guard.GuardError("workflow-base-changed")
    ctx["workflow_sha"] = workflow_sha
    return ctx


def assert_current(api, ctx: dict) -> None:
    current = pr_context(api, positive_id(ctx["pr"]))
    if any(current[k] != ctx[k] for k in current):
        raise guard.GuardError("pull-request-tuple-changed")


def binding(analysis: dict, mode: str) -> dict:
    ctx, result = analysis["context"], analysis["inspection"]
    return {**{k: ctx[k] for k in ("repository_id", "pr", "base", "head", "merge")},
            "base_policy": digest(result["base_policy"]),
            "candidate_policy": digest(result["candidate_policy"]), "mode": mode}


def existing_checks(api, merge_sha: str) -> list[dict]:
    checks = []
    for page in range(1, 11):
        result = api("GET", API_ROOT + f"/commits/{guard.sha(merge_sha)}/check-runs?filter=all&per_page=100&page={page}")
        checks.extend(result["check_runs"])
        if len(checks) >= result["total_count"]:
            return checks
    raise guard.GuardError("check-budget-exceeded")


def write_check(api, checks: list[dict], app_id: int, analysis: dict, name: str,
                mode: str, conclusion: str) -> None:
    identity = guard.binding_id(binding(analysis, mode))
    merge_sha = analysis["context"]["merge"]
    matches = [c for c in checks if c.get("app", {}).get("id") == app_id
               and c.get("name") == name and c.get("head_sha") == merge_sha
               and c.get("external_id") == identity]
    if len(matches) > 1:
        raise guard.GuardError("duplicate-authority-check")
    payload = {"name": name, "status": "completed", "conclusion": conclusion,
               "completed_at": datetime.now(timezone.utc).isoformat(), "external_id": identity,
               "output": {"title": name + ": " + conclusion,
                          "summary": "Authority tuple\n```json\n" + json.dumps(binding(analysis, mode), sort_keys=True) + "\n```"}}
    if matches:
        created = api("PATCH", API_ROOT + f"/check-runs/{positive_id(matches[0]['id'])}", payload)
    else:
        payload["head_sha"] = merge_sha
        created = api("POST", API_ROOT + "/check-runs", payload)
    if created.get("app", {}).get("id") != app_id:
        raise guard.GuardError("unexpected-check-issuer")


def eligible_review(inspection: dict, mode: str) -> bool:
    reviewed, normal = inspection.get("reviewed"), inspection["normal"]
    if (not reviewed or reviewed.get("exit_code") != 0 or normal.get("exit_code") != 1
            or normal.get("permissions", {}).get("status") != "failed"):
        return False
    changes = normal["permissions"].get("changes", [])
    allowed = {"editable", "protected"} if mode == "protected" else {"editable", "protected", "immutable", "unclassified"}
    return bool(changes) and all(c.get("zone") in allowed for c in changes)


def publish(analysis: dict, read_api, app_api, app_id: int) -> str:
    positive_id(app_id)
    ctx, inspection = analysis["context"], analysis["inspection"]
    assert_current(read_api, ctx)
    checks = existing_checks(read_api, ctx["merge"])
    mode = ctx["mode"]
    approved = False
    if mode in {"protected", "governance"}:
        guard.validate_dispatch(**ctx["dispatch"])
        if digest(ctx["requested_policy"]) != inspection["candidate_policy"]:
            raise guard.GuardError("dispatch-policy-changed")
        if eligible_review(inspection, mode):
            assert_current(read_api, ctx)
            write_check(app_api, checks, app_id, analysis, guard.APPROVAL_NAME, mode, "success")
            approved = True
    elif mode not in {"auto", "check"}:
        raise guard.GuardError("invalid-publication-mode")
    for approval_mode in ("protected", "governance"):
        expected = guard.binding_id(binding(analysis, approval_mode))
        if eligible_review(inspection, approval_mode) and any(
                guard.approval_matches(c, app_id, expected, ctx["merge"]) for c in checks):
            approved = True
    conclusion = "success" if inspection["normal"]["exit_code"] == 0 or approved else "failure"
    assert_current(read_api, ctx)
    write_check(app_api, checks, app_id, analysis, guard.CHECK_NAME, "final", conclusion)
    return conclusion


def event_context(api) -> dict:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    return resolve_context(api, os.environ["GITHUB_EVENT_NAME"], event,
        actor=positive_id(os.environ["GITHUB_ACTOR_ID"]), attempt=positive_id(os.environ["GITHUB_RUN_ATTEMPT"]),
        ref=os.environ["GITHUB_REF"], workflow_sha=os.environ["AUTHORITY_WORKFLOW_SHA"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("context", "analyze", "publish"))
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    try:
        read_api = github_api(os.environ.get("AUTHORITY_READ_TOKEN", ""))
        if args.command == "publish":
            analysis = json.loads((args.evidence / "analysis.json").read_text())
            # 로컬 JSON만으로 수동 승인하지 않고 GitHub 이벤트를 다시 확인한다.
            if event_context(read_api) != analysis["context"]:
                raise guard.GuardError("publication-context-changed")
            result = publish(analysis, read_api, github_api(os.environ.get("AUTHORITY_APP_TOKEN", "")),
                             positive_id(os.environ["AUTHORITY_APP_ID"]))
            (args.evidence / "publication.json").write_text(json.dumps({"conclusion": result}) + "\n")
            print("permission-authority: " + result)
            return 0 if result == "success" else 1
        ctx = event_context(read_api)
        if args.command == "context":
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                output.write(f"eligible=true\npr={ctx['pr']}\n")
        else:
            with tempfile.TemporaryDirectory(prefix="authority-candidate-") as directory:
                root = Path(directory) / "candidate"
                guard.fetch_candidate(root, ctx["pr"], ctx["base"], ctx["merge"])
                inspection = guard.inspect_candidate(root, ctx["base"], ctx["merge"], Path(sys.executable))
                analysis = {"context": ctx, "inspection": inspection}
                (args.evidence / "analysis.json").write_text(json.dumps(analysis, indent=2) + "\n")
        return 0
    except Exception:
        # 후보 내용·API 응답·토큰이 예외/로그에 섞이지 않게 고정된 오류만 남긴다.
        (args.evidence / "error.json").write_text('{"error":"authority-operation-failed"}\n')
        print("authority-operation-failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
