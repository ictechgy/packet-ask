"""휠과 sdist가 무엇을 싣고 무엇을 안 싣는지 확인한다."""

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
    credentials = subprocess.run(
        ["packet-ask", "credentials", "status", "glm"],
        check=False,
        capture_output=True,
        text=True,
    )
    if credentials.returncode != 0 or "glm |" not in credentials.stdout:
        raise SystemExit("설치 산출물의 credentials status가 실패했습니다.")
    skill = files("packet_ask.data").joinpath("SKILL.md").read_text(encoding="utf-8")
    if "packet-ask" not in skill:
        raise SystemExit("wheel/sdist 에 SKILL.md 가 없습니다.")
    # 에이전트 지침은 기여자 문서다. 실리면 지침만 고쳐도 배포물이 바뀌어
    # 매번 배포 여부를 다시 판단하게 된다. 설치된 산출물에서 직접 본다.
    # 메타 테스트는 `dist/` 가 없으면 skip 하고 CI 는 빌드보다 테스트를 먼저
    # 돌린다. 그래서 실제 배포물을 지키는 것은 이 단언이다.
    if files("packet_ask").joinpath("AGENTS.md").is_file():
        raise SystemExit("wheel/sdist 에 AGENTS.md 가 실렸습니다.")
    print("ok")


if __name__ == "__main__":
    main()
