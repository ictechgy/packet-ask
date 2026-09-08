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


def _lexical_rejection(key: str) -> PolicyError:
    """계열 문장에 어휘 한계 문장을 붙인다.

    걸린 사람이 보는 유일한 표면이 이 메시지다. 한계를 문서에만 적으면
    게이트를 실제로 만난 사람은 그 문서를 읽지 않는다. 그래서 우회로
    (표현을 바꾸면 통과)와 오탐 방향(그 단어를 담은 검토 전용 문장도 막힘)을
    거절 지점에서 같이 말한다.

    매치된 원문은 싣지 않는다. 무엇을 매치했는지 되돌려 주면 질문이 stderr 와
    실패 봉투에 다시 등장하고, 계열 이름 하나로 고칠 방법은 이미 충분하다.
    """
    return PolicyError(message(key) + " " + message("policy_lexical_limit"))


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
        raise _lexical_rejection("policy_implementation")
    if _INCIDENT_RE.search(question or ""):
        raise _lexical_rejection("policy_incident")
    if mode == "review" and files_flag == "include-files":
        raise PolicyError(message("review_include_files"))
    if mode == "research" and files_flag == "files":
        raise PolicyError(message("research_files"))
    if mode == "research" and has_diff:
        raise PolicyError(message("research_diff"))
