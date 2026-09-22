"""Python 판정기 밖의 Actions 신뢰 경계를 회귀로 고정한다."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_authority_workflow_uses_only_main_code_and_readonly_actions_token():
    workflow = (ROOT / ".github/workflows/permission-authority.yml").read_text()
    triggers = workflow.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
    assert set(re.findall(r"^  (\w+):$", triggers, re.MULTILINE)) == {"workflow_run", "workflow_dispatch"}
    assert "workflows: [CI]" in triggers and "types: [completed]" in triggers
    assert "github.ref == 'refs/heads/main'" in workflow
    steps = re.split(r"^      - ", workflow, flags=re.MULTILINE)[1:]
    checkouts = [step for step in steps if "uses: actions/checkout@" in step]
    assert len(checkouts) == 2
    for checkout in checkouts:
        assert "ref: ${{ github.workflow_sha }}" in checkout
        assert "persist-credentials: false" in checkout
        assert len(re.findall(r"^          ref:", checkout, re.MULTILINE)) == 1
    permissions = re.findall(r"^\s+(contents|actions|pull-requests|checks|id-token): (\w+)$", workflow, re.MULTILINE)
    assert permissions and all(value == "read" for _, value in permissions)
    uses = re.findall(r"uses: ([^\n]+)", workflow)
    assert len(uses) == 5
    assert all(re.fullmatch(r"[\w/-]+@[a-f0-9]{40} # v[0-9.]+", value) for value in uses)


def test_authority_workflow_opens_scoped_app_only_after_analysis():
    workflow = (ROOT / ".github/workflows/permission-authority.yml").read_text()
    authority = workflow.split("\n  authority:\n", 1)[1]
    assert "needs: context" in authority and "if: needs.context.outputs.eligible == 'true'" in authority
    assert "environment: exitzero-authority" in authority
    assert "group: permission-authority-${{ needs.context.outputs.pr }}" in authority
    assert "cancel-in-progress: false" in authority
    assert authority.index("authority_app.py analyze") < authority.index("uses: actions/create-github-app-token@")
    assert authority.index("uses: actions/create-github-app-token@") < authority.index("authority_app.py publish")
    token = authority.split("uses: actions/create-github-app-token@", 1)[1].split("\n      - ", 1)[0]
    assert "client-id: ${{ vars.EXITZERO_AUTHORITY_CLIENT_ID }}" in token
    assert "private-key: ${{ secrets.EXITZERO_AUTHORITY_PRIVATE_KEY }}" in token
    assert "owner: ictechgy" in token and "repositories: packet-ask" in token
    assert dict(re.findall(r"permission-([\w-]+): (\w+)", token)) == {"contents": "read", "checks": "write"}
    assert "skip-token-revoke" not in token
    assert authority.count("secrets.") == 1


def test_authority_runner_install_requires_exact_wheel_hash():
    workflow = (ROOT / ".github/workflows/permission-authority.yml").read_text()
    requirement = (ROOT / ".github/authority-requirements.txt").read_text().strip()
    assert requirement == "exitzero==0.6.1 --hash=sha256:79d35744089708db194b567395c36d438f71c8e9d34e65975267f0cfc8593344"
    install = next(line for line in workflow.splitlines() if "uv pip install" in line)
    for option in ("--no-config", "--no-cache", "--require-hashes", "--only-binary :all:",
                   "--no-deps", "--index-url https://pypi.org/simple", "-r .github/authority-requirements.txt"):
        assert option in install
    assert '"$RUNNER_TEMP/authority-venv/bin/python" -I .github/scripts/authority_app.py analyze' in workflow
