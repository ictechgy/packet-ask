"""서브로 보내면 안 되는 작업 유형을 막는다."""

from __future__ import annotations

import re

from packet_ask.errors import PolicyError

_IMPLEMENTATION_RE = re.compile(
    r"(구현해|리팩터링해|패치를 적용|코드를 작성|"
    r"implement this|write the code|apply this patch|refactor the (code|repo))",
    re.IGNORECASE,
)
_INCIDENT_RE = re.compile(
    r"(장애 대응|운영 인시던트|production incident|pagerduty)",
    re.IGNORECASE,
)


def assert_allowed_task(
    mode: str,
    question: str,
    files_flag: str | None = None,
    has_diff: bool = False,
) -> None:
    """모드와 질문 조합이 서브 정책에 맞는지 검사한다."""
    if mode not in {"review", "research", "brainstorm", "paste", "doctor"}:
        raise PolicyError(f"알 수 없는 모드입니다: {mode}")
    if _IMPLEMENTATION_RE.search(question or ""):
        raise PolicyError("구현·패치 적용 요청은 서브로 보내지 않습니다.")
    if _INCIDENT_RE.search(question or ""):
        raise PolicyError("운영 장애 대응은 서브로 보내지 않습니다.")
    if mode == "review" and files_flag == "include-files":
        raise PolicyError("review는 --include-files 대신 --files 를 쓰세요.")
    if mode == "research" and files_flag == "files":
        raise PolicyError("research는 --files 대신 --include-files 를 쓰세요.")
    if mode == "research" and has_diff:
        raise PolicyError("research는 로컬 diff를 보내지 않습니다. --include-files 만 허용합니다.")
