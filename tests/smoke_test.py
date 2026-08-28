"""휠과 sdist가 CLI 엔트리포인트를 포함하는지 확인한다."""

from __future__ import annotations

import subprocess


def main() -> None:
    """설치한 `packet-ask --help` 가 종료 코드 0 이어야 한다."""
    completed = subprocess.run(
        ["packet-ask", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode or 1)
    if "review" not in completed.stdout:
        raise SystemExit("packet-ask --help 에 review 가 없습니다.")
    print("ok")


if __name__ == "__main__":
    main()
