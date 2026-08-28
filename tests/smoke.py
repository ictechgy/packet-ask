"""휠과 sdist가 CLI와 스킬 원문을 포함하는지 확인한다."""

from __future__ import annotations

import subprocess
from importlib.resources import files


def main() -> None:
    """설치한 `packet-ask --help` 와 패키지 안 SKILL.md 를 확인한다."""
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
    skill = files("packet_ask.data").joinpath("SKILL.md").read_text(encoding="utf-8")
    if "packet-ask" not in skill:
        raise SystemExit("wheel/sdist 에 SKILL.md 가 없습니다.")
    print("ok")


if __name__ == "__main__":
    main()
