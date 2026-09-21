"""이벤트의 비교 기준을 로컬 Git ref와 별도 증거 파일로 고정한다."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

REF = "refs/exitzero/test-integrity-base"


def git(root: Path, *args: str) -> str:
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(
        ["git", "-C", str(root), *args], env=env,
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode:
        raise ValueError("로컬 Git 기준을 확인할 수 없습니다")
    return result.stdout.strip()


def resolve(root: Path, reference: str) -> str:
    if not reference or reference.startswith("-"):
        raise ValueError("비교 기준이 필요합니다")
    return git(root, "rev-parse", "--verify", "--end-of-options", reference + "^{commit}")


def event_commit(root: Path, variable: str) -> str:
    value = os.environ.get(variable, "")
    if not re.fullmatch(r"[0-9a-f]{40}", value) or value == "0" * 40:
        raise ValueError("이벤트 기준 SHA가 없습니다")
    return resolve(root, value)


def prepare(root: Path, explicit: str | None) -> dict[str, str | int]:
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("저장소 루트에서 실행해야 합니다")
    # 전용 ref가 symbolic ref여도 실제 main을 변경하거나 삭제하지 않는다.
    git(root, "update-ref", "--no-deref", "-d", REF)
    directory = root / ".exitzero"
    if directory.is_symlink():
        raise ValueError("증거 디렉터리 심링크는 허용하지 않습니다")
    directory.mkdir(exist_ok=True)
    output = directory / "test-integrity-base.json"
    output.unlink(missing_ok=True)
    head = resolve(root, "HEAD")
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if explicit is not None:
        if event:
            raise ValueError("CI 이벤트 기준은 로컬 옵션으로 바꾸지 않습니다")
        baseline, selection = resolve(root, explicit), "local_explicit"
    elif event == "pull_request":
        baseline = event_commit(root, "EXITZERO_CI_PR_BASE")
        selection = "pull_request_base"
    elif event == "push" and os.environ.get("GITHUB_REF") == "refs/heads/main":
        baseline = event_commit(root, "EXITZERO_CI_PUSH_BEFORE")
        selection = "main_push_before"
    elif not event or (event == "push" and os.environ.get("GITHUB_REF", "").startswith("refs/heads/")):
        baseline = git(root, "merge-base", head, resolve(root, "origin/main"))
        selection = "branch_merge_base" if event else "local_merge_base"
    else:
        raise ValueError("지원하지 않는 CI 이벤트입니다")
    git(root, "merge-base", "--is-ancestor", baseline, head)
    if selection in {"pull_request_base", "main_push_before"} and baseline == head:
        raise ValueError("CI 기준과 후보 커밋이 같습니다")
    if resolve(root, "HEAD") != head:
        raise ValueError("준비 중 후보 커밋이 바뀌었습니다")
    record = {"schema_version": 1, "status": "ready", "ref": REF,
              "base_sha": baseline, "head_sha": head, "selection": selection}
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".integrity-base-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(record, sort_keys=True) + "\n")
        git(root, "update-ref", "--no-deref", REF, baseline)
        os.replace(temporary, output)
    except (OSError, ValueError, subprocess.SubprocessError):
        git(root, "update-ref", "--no-deref", "-d", REF)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="로컬에서 사용할 명시적 비교 커밋/ref")
    args = parser.parse_args()
    try:
        record = prepare(Path.cwd().resolve(), args.base)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("기준 커밋을 준비하지 못했습니다. Git 이력과 CI 이벤트 기준을 확인하세요.", file=sys.stderr)
        return 2
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
