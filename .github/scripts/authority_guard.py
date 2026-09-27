"""후보 Git 객체는 데이터로만 읽고 고정된 권한 검사기를 실행한다."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

POLICY = ".github/authority.toml"
APPROVAL_NAME = "permission-authority-approval"
CHECK_NAME = "permission-authority"
MAX_FILES = 2000
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024


class GuardError(ValueError):
    """내용이나 자격 증명을 포함하지 않는 검사 실패."""


def safe_environment() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0")
    return env


def git(root: Path, *args: str, timeout: int = 30) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
         "-c", "core.attributesFile=" + os.devnull, "-C", str(root), *args],
        env=safe_environment(), capture_output=True, timeout=timeout,
    )
    if result.returncode or len(result.stdout) > MAX_TOTAL_BYTES:
        raise GuardError("git-inspection-failed")
    return result.stdout


def sha(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{40}", value):
        raise GuardError("invalid-commit")
    return value


def tree(root: Path, commit: str) -> list[tuple[str, str, str]]:
    from exitzero.files import is_sensitive
    entries = []
    folded = set()
    for raw in git(root, "ls-tree", "-rz", "--full-tree", sha(commit)).split(b"\0"):
        if not raw:
            continue
        header, path_bytes = raw.split(b"\t", 1)
        mode, kind, blob = header.decode("ascii").split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise GuardError("non-regular-git-entry")
        try:
            path = path_bytes.decode("ascii")
        except UnicodeError:
            raise GuardError("unsupported-git-path") from None
        pure = PurePosixPath(path)
        if (len(path_bytes) > 1024 or path.startswith("/") or ":" in path or "\\" in path
                or any(ord(c) < 32 or ord(c) == 127 for c in path)
                or any(p in {".", ".."} or p.casefold() == ".git" for p in pure.parts)
                or is_sensitive(Path(path)) or path.casefold() in folded):
            raise GuardError("unsupported-git-path")
        folded.add(path.casefold())
        entries.append((path, sha(blob), mode))
        if len(entries) > MAX_FILES:
            raise GuardError("file-budget-exceeded")
    return entries


def blob(root: Path, identifier: str) -> bytes:
    length = int(git(root, "cat-file", "-s", sha(identifier)))
    if length > MAX_FILE_BYTES:
        raise GuardError("file-budget-exceeded")
    content = git(root, "cat-file", "blob", identifier)
    if len(content) != length:
        raise GuardError("blob-length-mismatch")
    return content


def materialize(root: Path, baseline: str, candidate: str) -> None:
    tree(root, baseline)
    entries = tree(root, candidate)
    total = 0
    for name, identifier, mode in entries:
        content = blob(root, identifier)
        total += len(content)
        if total > MAX_TOTAL_BYTES:
            raise GuardError("byte-budget-exceeded")
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        # root는 이 검사만 소유하는 빈 임시 저장소이며 모든 tree 경로를 먼저 검사했다.
        with target.open("xb") as stream:
            stream.write(content)
        target.chmod(0o755 if mode == "100755" else 0o644)
    git(root, "update-ref", "--no-deref", "refs/heads/candidate", sha(candidate))
    git(root, "symbolic-ref", "HEAD", "refs/heads/candidate")
    git(root, "read-tree", candidate)


def copy_local_git(source: Path, target: Path, baseline: str, candidate: str) -> None:
    """테스트용 로컬 객체 공급자. 운영 CLI는 고정 GitHub URL만 사용한다."""
    target.mkdir()
    git(target, "init", "-q")
    git(target, "fetch", "--no-tags", "--no-recurse-submodules", str(source), sha(baseline), sha(candidate))
    materialize(target, baseline, candidate)


def verify_merge(root: Path, candidate: str, baseline: str, head: str, merge_tree: str) -> None:
    parents = git(root, "show", "-s", "--format=%P", sha(candidate)).decode().strip().split()
    if parents != [sha(baseline), sha(head)]:
        raise GuardError("merge-parents-mismatch")
    if git(root, "rev-parse", candidate + "^{tree}").decode().strip() != sha(merge_tree):
        raise GuardError("merge-tree-mismatch")


def fetch_candidate(target: Path, pr: int, baseline: str, candidate: str,
                    head: str, merge_tree: str) -> None:
    if type(pr) is not int or not 0 < pr < 1000000000:
        raise GuardError("invalid-pull-request")
    target.mkdir()
    git(target, "init", "-q")
    git(target, "fetch", "--no-tags", "--no-recurse-submodules", "https://github.com/ictechgy/packet-ask.git",
        sha(baseline) + ":refs/authority/base", f"refs/pull/{pr}/merge:refs/authority/merge", timeout=120)
    if git(target, "rev-parse", "refs/authority/merge").decode().strip() != sha(candidate):
        raise GuardError("merge-ref-changed")
    verify_merge(target, candidate, baseline, head, merge_tree)
    materialize(target, baseline, candidate)


def policy_bytes(root: Path, commit: str) -> bytes:
    found = [identifier for name, identifier, _ in tree(root, commit) if name == POLICY]
    if len(found) != 1:
        raise GuardError("authority-policy-missing")
    raw = blob(root, found[0])
    if len(raw) > 1024 * 1024:
        raise GuardError("authority-policy-too-large")
    return raw


def safe_policy(raw: bytes) -> dict:
    from exitzero.policy import parse_policy
    try:
        policy = parse_policy(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise GuardError("unsafe-authority-policy") from None
    if (set(policy) != {"version", "plugins", "checks", "permissions"}
            or policy["plugins"] != ["exitzero_verify"]
            or policy["checks"] != [{"id": "authority-syntax", "kind": "python.syntax", "paths": ["src/**/*.py"]}]):
        raise GuardError("unsafe-authority-policy")
    return policy


def execute_gate(root: Path, baseline: str, python: Path) -> dict:
    result = subprocess.run(
        [str(python), "-I", "-m", "exitzero", "--root", str(root), "--policy", POLICY,
         "check", "--trust-base", sha(baseline), "--format", "json"],
        cwd=root.parent, env=safe_environment(), capture_output=True, text=True, timeout=120,
    )
    if len(result.stdout) > 16 * 1024 * 1024:
        raise GuardError("receipt-budget-exceeded")
    receipt = json.loads(result.stdout)
    if (result.returncode not in {0, 1, 2} or receipt.get("exit_code") != result.returncode
            or receipt.get("tool_version") != "0.6.1"):
        raise GuardError("invalid-gate-receipt")
    saved = root / receipt["receipt"]
    if saved.is_symlink() or json.loads(saved.read_text())["run_id"] != receipt["run_id"]:
        raise GuardError("missing-persisted-receipt")
    return receipt


def inspect_candidate(root: Path, baseline: str, candidate: str, python: Path) -> dict:
    from exitzero.files import match_path
    original = policy_bytes(root, baseline)
    safe_policy(original)
    proposed = policy_bytes(root, candidate)
    result = {"base_policy": hashlib.sha256(original).hexdigest(),
              "candidate_policy": hashlib.sha256(proposed).hexdigest(),
              "normal": execute_gate(root, baseline, python), "reviewed": None}
    try:
        policy = safe_policy(proposed)
        zones = policy["permissions"]
        if any(not any(match_path(name, zones[z]) for z in zones)
               for name, _, _ in tree(root, candidate)):
            raise GuardError("unclassified-reviewed-path")
        result["reviewed"] = execute_gate(root, candidate, python)
    except GuardError as error:
        result["reviewed_error"] = str(error)
    return result


def binding_id(binding: dict) -> str:
    return "exitzero-authority-v2:" + hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def approval_matches(check: dict, app_id: int, expected: str, head_sha: str) -> bool:
    return (check.get("app", {}).get("id") == app_id and check.get("name") == APPROVAL_NAME
            and check.get("external_id") == expected and check.get("head_sha") == head_sha
            and check.get("status") == "completed" and check.get("conclusion") == "success")


def validate_dispatch(owner: int, actor: int, sender: int, attempt: int, ref: str) -> None:
    if not (type(owner) is int and owner > 0 and owner == actor == sender and attempt == 1
            and ref == "refs/heads/main"):
        raise GuardError("unauthorized-approval-dispatch")
