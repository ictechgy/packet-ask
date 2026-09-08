"""모드·질문 정책 게이트."""

import pytest

from packet_ask.errors import PolicyError
from packet_ask.policy import assert_allowed_task


def test_review_question_is_allowed() -> None:
    """리뷰 질문은 통과한다."""
    assert_allowed_task("review", "이 diff의 경쟁 상태를 찾아줘")


def test_rejects_implementation_request() -> None:
    """구현 요청은 정책 거부한다."""
    with pytest.raises(PolicyError, match="Implementation"):
        assert_allowed_task("review", "이 버그를 고치도록 구현해줘")


@pytest.mark.parametrize(
    "question",
    [
        "fix this bug",
        "make a patch for this",
        "이 코드를 수정해줘",
        "이 버그를 고쳐줘",
    ],
)
def test_rejects_common_implementation_imperatives(question: str) -> None:
    """흔한 영문·한글 구현 명령도 정책이 거절한다."""
    with pytest.raises(PolicyError):
        assert_allowed_task("review", question)


def test_research_rejects_implicit_files_flag_name() -> None:
    """리서치에 파일 첨부는 include-files 로만 허용한다."""
    with pytest.raises(PolicyError):
        assert_allowed_task("research", "조사해줘", files_flag="files")


def test_research_allows_include_files() -> None:
    """명시적 include-files 는 리서치에서 허용한다."""
    assert_allowed_task("research", "이 제안에 대한 외부 자료", files_flag="include-files")


def test_research_rejects_diff() -> None:
    """리서치는 로컬 diff 를 보내지 않는다."""
    with pytest.raises(PolicyError, match="diff"):
        assert_allowed_task("research", "조사해줘", has_diff=True)


@pytest.mark.parametrize(
    "question",
    [
        "구현해 주지 말고 검토만 해라",
        "코드를 작성하지 말고 분석만 해줘",
        "Do not implement this, just review it",
    ],
)
def test_negated_review_wording_is_still_rejected(question: str) -> None:
    """재현된 오탐을 **의도된 동작**으로 고정한다.

    검토만 해달라는 문장인데 목록 단어를 담았으므로 걸린다. 사용자에게는
    불편하지만 아래 `test_negation_would_let_implementation_through` 이
    보여주는 이유 때문에 열어 둘 수 없다. 오탐이라는 사실과 그 이유를 함께
    남기지 않으면 다음 사람이 "고장" 으로 보고 고치려 든다.
    """
    with pytest.raises(PolicyError):
        assert_allowed_task("review", question)


@pytest.mark.parametrize(
    "question",
    [
        "구현하지 말고 이 버그를 고쳐줘",
        "이 버그를 고치지 말고 구현해줘",
        "don't fix this bug, just review it",
    ],
)
def test_negation_would_let_implementation_through(question: str) -> None:
    """부정문을 허용하면 이 요청들이 통과한다. 그래서 허용하지 않는다.

    전부 실제로 구현·수정을 시키는 문장이다. 부정어를 보면 앞의 오탐 셋은
    통과하지만 이것들도 함께 통과한다. 게이트가 지금보다 약해진다.
    `_IMPLEMENTATION_RE` 에 부정문 처리를 넣지 않는 것이 합의된 결정이고
    이 테스트가 그 결정을 든다.
    """
    with pytest.raises(PolicyError):
        assert_allowed_task("review", question)


def test_gate_coverage_is_uneven_and_that_is_accepted() -> None:
    """같은 뜻의 문장인데 어떤 것은 걸리고 어떤 것은 지나간다.

    어휘 게이트의 실제 모양이다. 의미 판정으로 고치려 들지 않는다 —
    그러려면 부정문을 봐야 하고 그러면 위 테스트의 요청들이 통과한다.
    이 비대칭을 재현해 두는 이유는, 나중에 누가 한쪽만 보고 "버그" 라고
    판단해서 목록을 넓히거나 좁히려 할 때 전체 모양을 보기 위해서다.
    """
    assert_allowed_task("review", "이 diff를 구현하지 말고 보안 관점에서 검토만 해줘")
    with pytest.raises(PolicyError):
        assert_allowed_task("review", "구현해 주지 말고 검토만 해라")


@pytest.mark.parametrize(
    "question",
    ["이 버그를 고쳐줘", "production incident 대응해줘"],
)
def test_policy_rejection_states_the_bypass_at_the_gate(question: str) -> None:
    """거절 메시지 자체가 어휘 한계와 우회로를 말한다.

    문서에만 적으면 게이트를 실제로 만난 사람은 그 문서를 못 읽는다. 걸린
    사람이 보는 유일한 표면이 이 메시지다. 두 계열 모두 같은 고정 문장을
    싣는지 카탈로그에서 꺼내 확인한다. 사용자 입력은 싣지 않는다 — 무엇을
    매치했는지 원문을 되돌려 주면 질문이 stderr·JSON 에 다시 등장한다.
    """
    from packet_ask.text import message

    with pytest.raises(PolicyError) as exc:
        assert_allowed_task("review", question)
    assert message("policy_lexical_limit") in str(exc.value)
    assert question not in str(exc.value)
