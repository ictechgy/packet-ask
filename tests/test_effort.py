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
    assert receipt["effort_source"] == "explicit"
    assert receipt["timeout_seconds"] == 1800
    entry = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert entry["effort"] == "high"
    assert entry["effort_source"] == "explicit"


def test_preview_reports_effort_before_the_vendor_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """20분짜리를 걸기 전에 어떤 effort 로 나가는지 보여야 의미가 있다."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    assert main(_argv("--effort", "max", "--preview", "--json")) == codes.SUCCESS
    preview = json.loads(capsys.readouterr().out)["preview"]
    assert preview["effort"] == "max"
    assert preview["effort_source"] == "explicit"
    assert preview["timeout_seconds"] == 2700


def test_omitted_effort_is_null_with_an_explicit_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """값과 출처를 나눈다. `timeout_seconds` + `timeout_source` 와 같은 짝이다.

    한 필드에 섞으면 enum 도메인에 sentinel 이 들어가고, 출처가 늘어날 때
    값 도메인을 깨야 한다. 이 저장소는 키를 지우거나 이름을 바꾸지 않는다.
    """
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    assert main(_argv("--preview", "--json")) == codes.SUCCESS
    preview = json.loads(capsys.readouterr().out)["preview"]
    assert preview["effort"] is None
    assert preview["effort_source"] == "vendor-default"


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


def test_effort_actually_reaches_the_spawned_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """술어가 아니라 결로를 고정한다.

    `glm_argv("max")` 만 단언하면 `launch_glm` 이 `glm_argv()` 로 회귀해도
    테스트가 전부 통과한다. 실제로 그 뮤테이션에서 495개가 다 녹색이었다.
    자식에게 넘어가는 argv 를 잡는다.
    """
    from packet_ask import launch

    seen: dict[str, list[str]] = {}

    def capture(executable, argv, stdin_text, cwd, env, timeout):  # noqa: ANN001
        seen["argv"] = list(argv)
        return "리뷰 결과"

    monkeypatch.setattr(launch, "run_isolated_command", capture)
    monkeypatch.setattr(launch, "require_launchable", lambda _name: None)
    monkeypatch.setattr(launch, "_require_glm_key", lambda _source: "k" * 40)
    monkeypatch.setattr(launch, "_require_executable", lambda _name: Path("/usr/bin/true"))

    packet = _FakePacket()
    launch.launch_glm(packet, 60, "env", "xhigh")
    assert seen["argv"][:2] == ["--effort", "xhigh"]

    seen.clear()
    launch.launch_glm(packet, 60, "env", None)
    assert "--effort" not in seen["argv"]


def test_claude_launcher_carries_effort_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`claude` 는 같은 argv 를 재활용한다. glm 만 고쳐도 통과하면 안 된다."""
    from packet_ask import launch

    seen: dict[str, list[str]] = {}

    def capture(executable, argv, stdin_text, cwd, env, timeout):  # noqa: ANN001
        seen["argv"] = list(argv)
        return "리뷰 결과"

    monkeypatch.setattr(launch, "run_isolated_command", capture)
    monkeypatch.setattr(launch, "require_launchable", lambda _name: None)
    monkeypatch.setattr(launch, "_require_claude_key", lambda _source: "k" * 40)
    monkeypatch.setattr(launch, "_require_executable", lambda _name: Path("/usr/bin/true"))

    launch.launch_claude(_FakePacket(), 60, "env", "high")
    assert seen["argv"][:2] == ["--effort", "high"]


def test_kimi_refuses_effort_instead_of_dropping_it() -> None:
    """CLI 가 이미 막지만 launch.py 는 직접 import 가능한 공개 모듈이다.

    프로토콜을 4인자로 통일했으므로 kimi 도 effort 를 받는다. 받아서 무시하면
    조용한 드랍이 된다. `--include-files` 가 그렇게 버려졌던 전례가 있다.
    """
    from packet_ask.errors import PacketAskError
    from packet_ask.launch import launch_kimi

    with pytest.raises(PacketAskError) as caught:
        launch_kimi(_FakePacket(), 60, "env", "low")
    assert message("effort_unsupported") in str(caught.value)


def test_every_registered_launcher_takes_the_same_protocol() -> None:
    """새 런처를 3인자로 추가하면 dispatch 가 TypeError 로 죽는다.

    프로토콜 통일의 비용은 컴파일타임 강제가 없다는 것이다. 여기서 고정한다.
    """
    import inspect as _inspect

    from packet_ask import launch

    for name in ("launch_glm", "launch_claude", "launch_kimi"):
        params = list(_inspect.signature(getattr(launch, name)).parameters)
        assert params == ["packet", "timeout", "credential_source", "effort"], name


def test_effort_rejects_a_value_outside_the_enum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """열거 강제가 실제로 작동한다. 설계 44 가 이걸 근거로 든다."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit) as caught:
        main(_argv("--effort", "absurd"))
    assert caught.value.code == codes.USAGE


def test_effort_is_rejected_for_kimi_as_well(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """paste 만 보던 기존 테스트를 kimi 까지 넓힌다."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    argv = _argv("--effort", "max")
    argv[argv.index("glm")] = "kimi"
    assert main(argv) == codes.USAGE
    assert message("effort_unsupported") in capsys.readouterr().err


class _FakePacket:
    """launch 단위 테스트용 최소 packet."""

    root = Path("/tmp")

    def payload_text(self) -> str:
        return "packet"


def test_env_supplies_the_default_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """매번 치지 않아도 되게 하되, 출처가 기록돼야 조용한 기본값이 아니다."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_EFFORT", "high")
    assert main(_argv("--preview", "--json")) == codes.SUCCESS
    preview = json.loads(capsys.readouterr().out)["preview"]
    assert preview["effort"] == "high"
    assert preview["effort_source"] == "env"
    assert preview["timeout_seconds"] == 1800


def test_flag_wins_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--timeout` 이 explicit > auto 인 것과 같은 결이다."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_EFFORT", "max")
    assert main(_argv("--effort", "low", "--preview", "--json")) == codes.SUCCESS
    preview = json.loads(capsys.readouterr().out)["preview"]
    assert preview["effort"] == "low"
    assert preview["effort_source"] == "explicit"


def test_invalid_env_effort_is_rejected_not_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """사용자가 준 값이 쓸 수 없으면 거절한다. 조용한 기본값을 만들지 않는다.

    argparse `choices` 는 플래그만 본다. env 는 별도로 검증하지 않으면
    오타가 조용히 벤더 기본값으로 떨어진다. `PACKET_ASK_LEDGER` 가 잘못된
    경로일 때 벤더를 안 띄우는 것과 같은 결이어야 한다.
    """
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_EFFORT", "ultra")
    assert main(_argv("--preview")) == codes.USAGE
    captured = capsys.readouterr()
    assert message("effort_env_invalid") in captured.err
    assert captured.out == ""


def test_blank_env_effort_means_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """빈 문자열은 "설정하지 않음" 이다. 빈 값을 거절하면 unset 이 어려워진다."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_EFFORT", "   ")
    assert main(_argv("--preview", "--json")) == codes.SUCCESS
    preview = json.loads(capsys.readouterr().out)["preview"]
    assert preview["effort"] is None
    assert preview["effort_source"] == "vendor-default"


def test_env_effort_is_rejected_for_providers_that_cannot_take_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """env 로 들어와도 paste 에 조용히 실리면 안 된다.

    플래그 경로만 막으면 env 가 우회로가 된다.
    """
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_EFFORT", "max")
    argv = _argv("--preview")
    argv[argv.index("glm")] = "paste"
    assert main(argv) == codes.USAGE
    captured = capsys.readouterr()
    assert message("effort_unsupported") in captured.err
    assert captured.out == ""


def test_env_effort_does_not_reach_the_vendor_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """자식 환경은 화이트리스트다. 벤더가 이 변수를 자기 설정으로 읽으면 안 된다."""
    from packet_ask.paths import minimal_child_env

    monkeypatch.setenv("PACKET_ASK_EFFORT", "max")
    env = minimal_child_env(tmp_path)
    assert "PACKET_ASK_EFFORT" not in env


def test_env_effort_reaches_the_launcher_not_just_the_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """새 테스트 6개가 전부 `--preview` 라 `_finish_task` 결로를 안 봤다.

    preview 는 `_finish_task` 앞에서 끝난다. 그래서 호출부에서 effort 인자를
    빼도 507개가 전부 통과했다. `_finish_task` 의 파라미터가 기본값 None 이라
    조용히 사라진다 — 이 배치가 싸우는 "조용한 기본값" 이 정확히 이 모양이다.
    """
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_EFFORT", "xhigh")
    seen: dict[str, object] = {}

    def capture(packet, timeout, credential_source, effort):  # noqa: ANN001
        seen["effort"] = effort
        return "리뷰 결과"

    monkeypatch.setattr("packet_ask.cli.launch_glm", capture)
    assert main(_argv()) == codes.SUCCESS
    assert seen["effort"] == "xhigh"


def test_effort_levels_and_timeout_tiers_stay_in_lockstep() -> None:
    """레벨이 tier 에 없으면 `.get(..., 0)` 이 조용히 크기 tier 로 떨어진다.

    argparse `choices` 와 env 검증이 모두 `EFFORT_LEVELS` 를 쓰므로, 레벨을
    하나 더하면서 tier 를 안 더하면 그 레벨만 deadline 이 안 오른다.
    """
    from packet_ask.cli import EFFORT_LEVELS, EFFORT_TIMEOUT_SECONDS

    assert set(EFFORT_LEVELS) == set(EFFORT_TIMEOUT_SECONDS)


def test_invalid_env_message_names_every_level() -> None:
    """메시지가 레벨 목록을 하드코딩한다. 레벨이 바뀌면 메시지가 거짓말을 한다."""
    from packet_ask.cli import EFFORT_LEVELS

    text = message("effort_env_invalid")
    for level in EFFORT_LEVELS:
        assert level in text, level


def test_receipt_refuses_a_value_source_mismatch() -> None:
    """출처를 정확히 남기는 것이 이 배치의 존재 이유다. 어긋난 조합을 막는다."""
    from packet_ask.receipt import build_receipt

    with pytest.raises(ValueError):
        build_receipt(
            "glm", "files", [], None, _FakeBuiltPacket(),
            timeout_seconds=1200, timeout_source="auto", timeout_applies=True,
            surface="absent", effort=None, effort_source="explicit",
        )


def test_invalid_env_precedes_the_provider_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """paste + 잘못된 env 에서 어느 메시지가 나오는지 고정한다.

    둘 다 거절이라 결과는 같지만, 순서를 고정하지 않으면 나중에 가드를 옮길 때
    문구가 조용히 바뀐다. 0.5.1 에서 같은 이유로 순서 의존성을 고정한 적이 있다.
    """
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_EFFORT", "ultra")
    argv = _argv("--preview")
    argv[argv.index("glm")] = "paste"
    assert main(argv) == codes.USAGE
    captured = capsys.readouterr()
    assert message("effort_env_invalid") in captured.err
    assert message("effort_unsupported") not in captured.err
    assert captured.out == ""


def test_env_effort_does_not_reach_the_launcher_env(tmp_path: Path, monkeypatch) -> None:
    """런처가 실제로 쓰는 함수까지 내려가서 본다.

    `minimal_child_env` 만 보면 런처가 그것을 쓰는지는 확인하지 못한다.
    `isolated_env` 가 런처의 실제 호출 지점이다.
    """
    from packet_ask.launch import isolated_env

    monkeypatch.setenv("PACKET_ASK_EFFORT", "max")
    env = isolated_env(tmp_path, {})
    assert "PACKET_ASK_EFFORT" not in env


class _FakeBuiltPacket:
    """receipt 단위 테스트용 최소 packet."""

    report = None

    def payload_bytes(self) -> bytes:
        return b"packet"

    def payload_digest(self) -> str:
        return "a" * 64
