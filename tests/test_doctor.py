"""doctor 플래그 판정."""

from packet_ask.doctor import claude_supports_isolated_print, kimi_supports_print


def test_claude_help_with_required_flags() -> None:
    """필수 플래그가 있으면 실행 후보가 된다."""
    help_text = "--bare\n--tools\n--no-session-persistence\n--setting-sources\n"
    assert claude_supports_isolated_print(help_text) is True


def test_claude_help_missing_bare() -> None:
    """--bare 가 없으면 실행 후보가 아니다."""
    help_text = "--tools\n--no-session-persistence\n--setting-sources\n"
    assert claude_supports_isolated_print(help_text) is False


def test_kimi_detects_prompt_flag() -> None:
    """-p 가 있으면 print 모드는 있다."""
    assert kimi_supports_print("kimi -p prompt") is True


def test_kimi_isolated_print_needs_agent_file_and_workdir() -> None:
    """도구 없는 원샷은 agent-file 과 work-dir 이 필요하다."""
    from packet_ask.doctor import kimi_supports_isolated_print

    full = "--prompt\n--quiet\n--agent-file\n--work-dir\n"
    assert kimi_supports_isolated_print(full) is True
    assert kimi_supports_isolated_print("--prompt\n--quiet\n") is False
