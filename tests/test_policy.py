"""모드·질문 정책 게이트."""

import pytest

from packet_ask.errors import PolicyError
from packet_ask.policy import assert_allowed_task


def test_review_question_is_allowed() -> None:
    """리뷰 질문은 통과한다."""
    assert_allowed_task("review", "이 diff의 경쟁 상태를 찾아줘")


def test_rejects_implementation_request() -> None:
    """구현 요청은 정책 거부한다."""
    with pytest.raises(PolicyError, match="구현"):
        assert_allowed_task("review", "이 버그를 고치도록 구현해줘")


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
