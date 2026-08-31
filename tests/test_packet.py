"""패킷 디렉터리 생성과 스크럽 반영."""

import subprocess
from pathlib import Path

import pytest

from packet_ask.errors import BudgetError, RedactionFailed, ScopeError
from packet_ask.packet import _render_numbered_body, _render_selected_tree, build_packet
from packet_ask.scope import ScopedFile


def test_packet_rewrites_home_and_has_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """패킷은 홈 경로를 지우고 git 경계를 만든다."""

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    files = [ScopedFile(relative="src/app.py", content=f"log {home}/secret.py\n")]
    packet = build_packet(
        mode="review",
        question="이 파일의 경쟁 상태를 찾아줘",
        files=files,
        diff_text=None,
        parent=tmp_path / "packets",
    )
    written = (packet.root / "files" / "src" / "app.py").read_text(encoding="utf-8")
    assert (packet.root / "CLAUDE.md").read_text(encoding="utf-8") == ""
    assert str(home) not in written
    assert "[REDACTED HOME]" in written
    assert (packet.root / "CLAUDE.md").is_file()
    assert (packet.root / "TASK.md").is_file()
    assert (packet.root / "packet.md").is_file()
    assert (packet.root / ".git").is_dir()
    packet.destroy()
    assert not packet.root.exists()


def test_packet_md_contains_task_and_files(tmp_path: Path) -> None:
    """packet.md 는 질문과 파일 본문을 담는다."""
    files = [ScopedFile(relative="a.py", content="x = 1\n")]
    packet = build_packet(
        mode="research",
        question="이 제안에 대한 외부 자료를 조사해줘",
        files=files,
        diff_text=None,
        parent=tmp_path,
    )
    blob = (packet.root / "packet.md").read_text(encoding="utf-8")
    assert "외부 자료" in blob
    assert "a.py" in blob
    assert "x = 1" in blob
    packet.destroy()


def test_packet_line_numbers_are_opt_in_and_packet_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """명시한 경우 scrubbed 파일 본문만 번호를 붙이고 저장 파일은 그대로 둔다."""
    monkeypatch.setenv("PACKET_ASK_LANG", "en")
    files = [ScopedFile(relative="a.py", content="first\nowner@example.com\n")]
    plain = build_packet("review", "review", files, None, tmp_path / "plain")
    numbered = build_packet(
        "review",
        "review",
        files,
        None,
        tmp_path / "numbered",
        line_numbers=True,
    )
    try:
        assert "Packet-local line numbers" not in plain.payload_text()
        assert "## File: a.py\n\n```\nfirst\n[REDACTED EMAIL]\n\n```" in plain.payload_text()
        assert "1 | first\n2 | [REDACTED EMAIL]" in numbered.payload_text()
        stored = (numbered.root / "files" / "a.py").read_text(encoding="utf-8")
        assert stored == "first\n[REDACTED EMAIL]\n"
        assert "2 |" not in stored
        assert plain.payload_digest() != numbered.payload_digest()
    finally:
        plain.destroy()
        numbered.destroy()


@pytest.mark.parametrize(
    "body",
    [
        "",
        "one",
        "one\n",
        "\n",
        "one\n\n",
        "one\r\ntwo\r\n",
        "one\rtwo",
        "one\ftwo",
        "one\u2028two",
        "7 | content that resembles a gutter",
        "\n".join(str(number) for number in range(10)),
    ],
)
def test_numbered_gutter_can_be_stripped_without_changing_body(body: str) -> None:
    """gutter 제거는 LF, 빈 줄, 마지막 줄 종결을 포함해 원문을 복원한다."""
    numbered = _render_numbered_body(body)
    if body == "":
        assert numbered == ""
        return
    parts = numbered.split("\n")
    trailing_lf = numbered.endswith("\n")
    if trailing_lf:
        parts.pop()
    restored = "\n".join(line.split(" | ", 1)[1] for line in parts)
    if trailing_lf:
        restored += "\n"
    assert restored == body


def test_packet_line_numbers_do_not_decorate_diff(tmp_path: Path) -> None:
    """unified diff의 자체 old/new 줄 정보를 synthetic gutter로 덮지 않는다."""
    diff = "@@ -1 +1 @@\n-old\n+new\n"
    packet = build_packet(
        "review",
        "review",
        [],
        diff,
        tmp_path,
        line_numbers=True,
    )
    try:
        assert "Packet-local line numbers" not in packet.payload_text()
        assert diff in packet.payload_text()
    finally:
        packet.destroy()


def test_packet_line_number_overhead_obeys_final_byte_cap(tmp_path: Path) -> None:
    """번호 gutter와 설명문도 최종 packet byte budget 안에서 계산한다."""
    files = [ScopedFile(relative="a.py", content="one\ntwo\n")]
    plain = build_packet("review", "review", files, None, tmp_path / "plain")
    limit = len(plain.payload_bytes())
    plain.destroy()
    with pytest.raises(BudgetError):
        build_packet(
            "review",
            "review",
            files,
            None,
            tmp_path / "numbered",
            max_bytes=limit,
            line_numbers=True,
        )


def test_selected_tree_uses_only_explicit_manifest_and_is_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """트리는 filesystem walk 없이 명시 path만 정렬하고 중복을 한 번 표시한다."""
    monkeypatch.setenv("PACKET_ASK_LANG", "en")
    files = [
        ScopedFile(relative="tests/test_app.py", content="test\n"),
        ScopedFile(relative="src/lib/util.py", content="util\n"),
        ScopedFile(relative="src/app.py", content="app\n"),
        ScopedFile(relative="src/app.py", content="app\n"),
    ]
    packet = build_packet(
        "review",
        "review",
        files,
        None,
        tmp_path,
        selected_tree=True,
    )
    try:
        expected = "src/\n  app.py\n  lib/\n    util.py\ntests/\n  test_app.py"
        assert (
            f"Selected file tree (explicit files only):\n\n```text\n{expected}\n```"
            in packet.payload_text()
        )
        assert packet.payload_text().count("  app.py\n") == 1
        assert "unselected.py" not in packet.payload_text()
    finally:
        packet.destroy()


def test_selected_tree_escapes_control_and_markdown_segments() -> None:
    """manifest label은 tree 문맥 밖 새 줄이나 fence를 만들 수 없다."""
    rendered = _render_selected_tree(["src/line\nbreak.py", "src/```danger.py"])
    assert rendered == "src/\n  \\u0060\\u0060\\u0060danger.py\n  line\\nbreak.py"
    assert rendered.count("\n") == 2


@pytest.mark.parametrize(
    "path",
    ["", "./src/app.py", "src//app.py", "src/app.py/", "../app.py", "/app.py"],
)
def test_selected_tree_rejects_noncanonical_relative_paths(path: str) -> None:
    """빈 segment·정규화·상위/절대 path는 tree label이 되기 전에 거절한다."""
    with pytest.raises(RedactionFailed):
        _render_selected_tree([path])


def test_selected_tree_rejects_file_directory_collision() -> None:
    """같은 label을 파일과 디렉터리로 합쳐 manifest 정보를 잃지 않는다."""
    with pytest.raises(RedactionFailed):
        _render_selected_tree(["src", "src/app.py"])


def test_selected_tree_handles_deep_manifest_iteratively() -> None:
    """synthetic API 입력도 Python recursion limit과 무관하게 렌더한다."""
    deep = "/".join(["a"] * 1100 + ["leaf.py"])
    rendered = _render_selected_tree([deep])
    assert rendered.startswith("a/\n  a/")
    assert rendered.endswith("leaf.py")


def test_selected_tree_requires_files_at_packet_api(tmp_path: Path) -> None:
    """CLI 밖 build API에서도 selected tree의 silent no-op을 막는다."""
    with pytest.raises(ScopeError):
        build_packet(
            "review",
            "review",
            [],
            None,
            tmp_path,
            selected_tree=True,
        )
    assert list(tmp_path.iterdir()) == []


def test_selected_tree_combines_with_packet_line_numbers(tmp_path: Path) -> None:
    """tree section은 file section 앞에 있고 file gutter 의미를 바꾸지 않는다."""
    packet = build_packet(
        "review",
        "review",
        [ScopedFile(relative="src/app.py", content="one\ntwo\n")],
        None,
        tmp_path,
        line_numbers=True,
        selected_tree=True,
    )
    try:
        payload = packet.payload_text()
        assert payload.index("Selected file tree") < payload.index("## File: src/app.py")
        assert "1 | one\n2 | two" in payload
    finally:
        packet.destroy()


def test_selected_tree_default_is_byte_identical_and_overhead_is_capped(
    tmp_path: Path,
) -> None:
    """opt-in을 끄면 기존 byte를 유지하고 켜면 tree도 최종 budget에 포함한다."""
    files = [ScopedFile(relative="src/app.py", content="app\n")]
    implicit = build_packet("review", "review", files, None, tmp_path / "implicit")
    explicit = build_packet(
        "review",
        "review",
        files,
        None,
        tmp_path / "explicit",
        selected_tree=False,
    )
    limit = len(implicit.payload_bytes())
    try:
        assert implicit.payload_bytes() == explicit.payload_bytes()
    finally:
        implicit.destroy()
        explicit.destroy()
    with pytest.raises(BudgetError):
        build_packet(
            "review",
            "review",
            files,
            None,
            tmp_path / "tree",
            max_bytes=limit,
            selected_tree=True,
        )


def test_packet_diff_does_not_retain_unicode_adjacent_email(tmp_path: Path) -> None:
    """diff 조각과 최종 packet 모두 Unicode 인접 이메일 원문을 담지 않는다."""
    sample = "+한alice@example.com\n"
    packet = build_packet(
        mode="review",
        question="review",
        files=[],
        diff_text=sample,
        parent=tmp_path,
    )
    diff = (packet.root / "files" / "changes.patch").read_text(encoding="utf-8")
    assert "@example.com" not in diff
    assert "@example.com" not in packet.payload_text()
    assert "[REDACTED EMAIL]" in diff
    assert packet.report.emails == 1
    packet.destroy()


def test_packet_rejects_obfuscated_international_email(tmp_path: Path) -> None:
    """부분 redaction할 수 없는 Unicode mailbox는 packet/provider 전에 fail-closed한다."""
    local = "".join(chr(item) for item in (0x7528, 0x6237))
    domain = "".join(chr(item) for item in (0x4F8B, 0x5B50, 0x3002, 0x516C, 0x53F8))
    with pytest.raises(RedactionFailed, match="email"):
        build_packet(
            mode="review",
            question="review",
            files=[],
            diff_text=f"+{local}@{domain}\n",
            parent=tmp_path,
        )
    assert list(tmp_path.glob("packet-ask-*")) == []


def test_packet_rejects_format_obfuscated_secret_family(tmp_path: Path) -> None:
    """Cf로 끊은 token family도 최종 packet/provider 전에 fail-closed한다."""
    source = "eyJ" + "A" * 8 + "." + "B" * 8 + "." + "C" * 8
    obfuscated = source[:2] + chr(0x200B) + source[2:]
    with pytest.raises(RedactionFailed, match="secret"):
        build_packet(
            mode="review",
            question="review",
            files=[],
            diff_text="+" + obfuscated + "\n",
            parent=tmp_path,
        )
    assert list(tmp_path.glob("packet-ask-*")) == []


def test_packet_contract_uses_selected_language(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """패킷 내부 계약도 기본 영어와 명시 한글을 구분한다."""
    monkeypatch.setenv("PACKET_ASK_LANG", "en")
    english = build_packet("review", "review", [], None, tmp_path / "en")
    assert "Output rules:" in (english.root / "packet.md").read_text(encoding="utf-8")
    english.destroy()

    monkeypatch.setenv("PACKET_ASK_LANG", "ko")
    korean = build_packet("review", "리뷰", [], None, tmp_path / "ko")
    assert "출력 규칙:" in (korean.root / "packet.md").read_text(encoding="utf-8")
    korean.destroy()


def test_packet_stores_payload_away_from_control_files(tmp_path: Path) -> None:
    """수집 파일은 files/ 아래에 두어 CLAUDE.md 와 겹치지 않게 한다."""
    files = [ScopedFile(relative="CLAUDE.md", content="# user file\n")]
    packet = build_packet(
        mode="review",
        question="이 파일을 리뷰해줘",
        files=files,
        diff_text=None,
        parent=tmp_path,
    )
    assert (packet.root / "CLAUDE.md").read_text(encoding="utf-8") == ""
    assert "# user file" in (packet.root / "files" / "CLAUDE.md").read_text(encoding="utf-8")
    packet.destroy()


def test_git_boundary_uses_bounded_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """패킷 git init도 공통 bounded runner와 metadata 상한을 쓴다."""
    captured: dict[str, object] = {}

    def bounded(worktree: Path, extra: list[str], max_bytes: int) -> str:
        captured["worktree"] = worktree
        captured["extra"] = extra
        captured["max_bytes"] = max_bytes
        (worktree / ".git").mkdir()
        return ""

    monkeypatch.setattr("packet_ask.packet.run_bounded_git", bounded)
    files = [ScopedFile(relative="a.py", content="x = 1\n")]
    packet = build_packet(
        mode="review",
        question="이 파일을 리뷰해줘",
        files=files,
        diff_text=None,
        parent=tmp_path,
    )
    assert captured["extra"] == ["init"]
    assert captured["max_bytes"] == 4096
    packet.destroy()


def test_packet_git_boundary_runner_does_not_copy_parent_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """공통 runner로 수렴해도 packet git init의 최소 env 계약을 유지한다."""
    captured: list[dict[str, str]] = []
    real_popen = subprocess.Popen

    def spy(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        command = [str(part) for part in args[0]] if args else []  # type: ignore[index]
        env = kwargs.get("env")
        if "init" in command and isinstance(env, dict):
            captured.append(env)
        return real_popen(*args, **kwargs)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-secret")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "outside.git"))
    monkeypatch.setattr("packet_ask.scope.subprocess.Popen", spy)
    packet = build_packet("review", "review", [], None, tmp_path / "packets")
    assert captured
    assert all("ANTHROPIC_API_KEY" not in env for env in captured)
    assert all("parent-secret" not in env.values() for env in captured)
    assert all("GIT_DIR" not in env for env in captured)
    packet.destroy()


def test_packet_rejects_git_relative_path(tmp_path: Path) -> None:
    """.git 상대경로는 패킷에 쓰지 않는다."""
    files = [ScopedFile(relative=".git/config", content="[core]\n")]
    with pytest.raises(RedactionFailed):
        build_packet(
            mode="review",
            question="이 설정을 리뷰해줘",
            files=files,
            diff_text=None,
            parent=tmp_path,
        )


def test_packet_git_init_timeout_is_redaction_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """packet-local git init의 timeout은 traceback 대신 안정된 오류가 된다."""
    monkeypatch.setattr(
        "packet_ask.packet.run_bounded_git",
        lambda *_args: (_ for _ in ()).throw(ScopeError("timeout")),
    )
    with pytest.raises(RedactionFailed):
        build_packet("review", "review", [], None, tmp_path / "packets")
    assert list((tmp_path / "packets").glob("packet-ask-*")) == []


def test_packet_git_init_nonzero_is_redaction_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git init 실패는 CalledProcessError를 외부로 노출하지 않는다."""
    monkeypatch.setattr(
        "packet_ask.packet.run_bounded_git",
        lambda *_args: (_ for _ in ()).throw(BudgetError("too large")),
    )
    with pytest.raises(RedactionFailed):
        build_packet("review", "review", [], None, tmp_path / "packets")


def test_built_packet_reuses_cached_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """receipt와 launch용 payload·digest를 packet.md에서 반복해 읽지 않는다."""
    packet = build_packet("review", "review", [], None, tmp_path / "packets")

    def fail_read(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("packet.md must use the in-memory payload")

    monkeypatch.setattr(Path, "read_text", fail_read)
    monkeypatch.setattr(Path, "read_bytes", fail_read)
    assert "# Task" in packet.payload_text()
    assert packet.payload_bytes().startswith(b"# Task")
    assert len(packet.payload_digest()) == 64
    packet.destroy()
