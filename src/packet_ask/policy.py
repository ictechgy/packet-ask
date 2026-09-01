"""서브로 보내면 안 되는 작업 유형을 막는다."""

from __future__ import annotations

import re

from packet_ask.errors import PolicyError
from packet_ask.text import message

_IMPLEMENTATION_RE = re.compile(
    r"(구현해|리팩터링해|패치를 적용|패치(?:를)? 만들어|코드를 작성|"
    r"(?:코드|버그)(?:을|를)?\s*(?:수정|고쳐)|수정해\s*줘|고쳐\s*줘|"
    r"implement this|write the code|apply this patch|refactor the (?:code|repo)|"
    r"fix (?:this|the) (?:bug|code)|make (?:a )?patch)",
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
    if mode not in {"review", "research", "doctor"}:
        raise PolicyError(message("policy_unknown_mode", mode=mode))
    if _IMPLEMENTATION_RE.search(question or ""):
        raise PolicyError(message("policy_implementation"))
    if _INCIDENT_RE.search(question or ""):
        raise PolicyError(message("policy_incident"))
    if mode == "review" and files_flag == "include-files":
        raise PolicyError(message("review_include_files"))
    if mode == "research" and files_flag == "files":
        raise PolicyError(message("research_files"))
    if mode == "research" and has_diff:
        raise PolicyError(message("research_diff"))
