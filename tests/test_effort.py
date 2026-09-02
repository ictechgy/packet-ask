"""추론 effort 노브와 그것이 timeout tier 에 미치는 영향."""

import json
from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask.cli import main
from packet_ask.text import message

from test_cli import _init_repo


def _argv(*extra: str) -> list[str]:
    return [
        "review", "--provider", "glm", "--files", "src/app.py",
        "--question", "리뷰해줘", *extra,
    ]


def test_effort_is_a_bounded_enum_not_free_text() -> None:
    """열거값이라 벤더 argv 주입 표면이 없다.

    `--model` 처럼 임의 문자열을 넘기면 검증 부담이 생긴다. 실측한 값만 받는다.
    """
    from packet_ask.cli import EFFORT_LEVELS

    assert EFFORT_LEVELS == ("low", "medium", "high", "xhigh", "max")


def test_effort_raises_the_auto_timeout_tier() -> None:
    """2,663 바이트 패킷에서 low 108초, max 751초를 실측했다.

    크기는 effort 를 고정하면 20배 늘어도 1.2~1.35배였다. 즉 지금 tier 는
    약한 변수로 나누고 강한 변수를 안 보고 있었다. 관측 최악 903초에
    3배 안팎 여유를 둔다.
    """
    from packet_ask.cli import _resolve_timeout

    assert _resolve_timeout(None, 1000, None) == (1200, "auto")
    assert _resolve_timeout(None, 1000, "low") == (1200, "auto")
    assert _resolve_timeout(None, 1000, "medium") == (1200, "auto")
    assert _resolve_timeout(None, 1000, "high") == (1800, "auto")
    assert _resolve_timeout(None, 1000, "xhigh") == (2700, "auto")
    assert _resolve_timeout(None, 1000, "max") == (2700, "auto")


def test_effort_tier_never_lowers_the_size_tier() -> None:
    """큰 패킷의 기존 상한을 effort 가 끌어내리면 회귀다. 둘 중 큰 값을 쓴다."""
    from packet_ask.cli import _resolve_timeout

    big = 200 * 1024
    assert _resolve_timeout(None, big, None) == (1800, "auto")
    assert _resolve_timeout(None, big, "low") == (1800, "auto")
    assert _resolve_timeout(None, big, "max") == (2700, "auto")


def test_explicit_timeout_still_wins_over_effort() -> None:
    """명시값은 clamp 없이 그대로 존중한다는 기존 계약을 깨지 않는다."""
    from packet_ask.cli import _resolve_timeout

    assert _resolve_timeout(30, 1000, "max") == (30, "explicit")


def test_effort_reaches_the_vendor_argv() -> None:
    """플래그가 자식 argv 에 실제로 실린다. 실측으로 Z.ai 가 이것을 존중했다."""
    from packet_ask.launch import glm_argv

    assert glm_argv(None)[:2] != ["--effort", "None"]
    assert "--effort" not in glm_argv(None)
    argv = glm_argv("max")
    assert argv[:2] == ["--effort", "max"]
    # 나머지 격리 플래그가 그대로 남아 있어야 한다.
    for flag in ("--bare", "--tools", "--permission-mode", "--strict-mcp-config"):
        assert flag in argv


def test_effort_is_recorded_on_every_machine_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """무엇을 하고 보냈는지가 기계 표면에 없으면 안 된다. 0.6.0 논지 그대로."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("PACKET_ASK_LEDGER", str(ledger))
    monkeypatch.setattr("packet_ask.cli.launch_glm", lambda *a, **k: "리뷰 결과")

    assert main(_argv("--effort", "high", "--json")) == codes.SUCCESS
    receipt = json.loads(capsys.readouterr().out)["receipt"]
    assert receipt["effort"] == "high"
    assert receipt["timeout_seconds"] == 1800
    entry = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert entry["effort"] == "high"


def test_preview_reports_effort_before_the_vendor_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """20분짜리를 걸기 전에 어떤 effort 로 나가는지 보여야 의미가 있다."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    assert main(_argv("--effort", "max", "--preview", "--json")) == codes.SUCCESS
    preview = json.loads(capsys.readouterr().out)["preview"]
    assert preview["effort"] == "max"
    assert preview["timeout_seconds"] == 2700


def test_effort_without_it_stays_absent_not_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """생략하면 벤더 기본값이다. 우리가 고른 값이 있는 것처럼 보이면 안 된다."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    assert main(_argv("--preview", "--json")) == codes.SUCCESS
    preview = json.loads(capsys.readouterr().out)["preview"]
    assert preview["effort"] == "vendor-default"


def test_effort_is_rejected_for_providers_that_cannot_take_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """paste 와 kimi 는 이 플래그를 받지 않는다. 조용히 버리지 않는다.

    `--include-files` 가 조용히 버려졌던 것이 이 저장소의 실제 결함이었다.
    """
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    argv = _argv("--effort", "max")
    argv[argv.index("glm")] = "paste"
    assert main(argv) == codes.USAGE
    assert message("effort_unsupported") in capsys.readouterr().err
