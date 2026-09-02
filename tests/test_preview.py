"""벤더를 실행하지 않는 런치 계획 미리보기."""

import json
from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask.cli import main
from packet_ask.text import message

from test_cli import _init_repo


def _preview(repo: Path, *extra: str) -> list[str]:
    """review --preview 를 돌리고 argv 를 만든다."""
    return [
        "review",
        "--provider",
        "glm",
        "--preview",
        "--files",
        "src/app.py",
        "--question",
        "리뷰해줘",
        *extra,
    ]


def test_preview_never_launches_a_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """미리보기의 존재 이유는 20분짜리 실패를 시작하기 전에 막는 것이다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)

    def fail_launch(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("preview must not launch a provider")

    # 하나만 막으면 dispatch 가 바뀌었을 때 가드를 놓친다. 셋 다 막는다.
    for launcher in ("launch_glm", "launch_claude", "launch_kimi"):
        monkeypatch.setattr(f"packet_ask.cli.{launcher}", fail_launch)
    assert main(_preview(repo)) == codes.SUCCESS
    out = capsys.readouterr()
    assert out.out.startswith("packet-ask preview ")
    # 패킷 본문도 불신뢰 봉투도 나가지 않는다.
    assert "UNTRUSTED PROVIDER OUTPUT" not in out.out
    assert "print(1)" not in out.out
    assert "packet-ask timing" not in out.err


def test_preview_does_not_read_a_credential_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """존재만 본다. 값을 읽으면 미리보기가 실행만큼 위험해진다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)

    def fail_resolve(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("preview must not resolve a credential value")

    # 값을 실제로 꺼내는 경로와 그것을 부르는 런처를 둘 다 막는다.
    monkeypatch.setattr("packet_ask.keysource.resolve_provider_key", fail_resolve)
    monkeypatch.setattr("packet_ask.launch.resolve_provider_key", fail_resolve)
    assert main(_preview(repo)) == codes.SUCCESS


def test_preview_writes_no_ledger_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """대장은 egress 지점 도달을 뜻한다. 미리보기는 거기에 도달하지 않는다.

    한 줄이라도 남기면 "무엇이 나갔나" 라는 대장의 질문에 나가지 않은 것이
    섞인다. 그러면 감사 표면으로서의 의미가 사라진다.
    """
    # 대장은 워크트리 밖이어야 한다. 저장소를 하위 디렉터리로 만들어야
    # tmp_path 가 실제로 바깥이 된다. 이전에는 tmp_path 자체가 저장소라
    # 경로가 애초에 거절될 값이었고, preview 가 그 검사 앞에서 반환하는 바람에
    # 테스트가 헛통과했다. 아래 양성 대조가 그것을 잡는다.
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("PACKET_ASK_LEDGER", str(ledger))

    assert main(_preview(repo)) == codes.SUCCESS
    assert not ledger.exists()

    # 양성 대조. env 이름이 틀렸으면 위 단언은 아무것도 증명하지 않는다.
    # 같은 env 로 preview 없이 돌리면 반드시 한 줄이 남아야 한다.
    argv = [item for item in _preview(repo) if item != "--preview"]
    argv[argv.index("glm")] = "paste"
    assert main(argv) == codes.SUCCESS
    assert ledger.exists()
    assert len(ledger.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_preview_reports_the_launch_plan_without_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """inspect 가 일부러 안 만지는 provider·timeout·credential 을 채운다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PACKET_ASK_GLM_KEY", "x" * 40)

    assert main(_preview(repo, "--json")) == codes.SUCCESS
    data = json.loads(capsys.readouterr().out)
    assert data["schema"] == "packet-ask.v1"
    assert data["ok"] is True
    preview = data["preview"]
    assert preview["provider"] == "glm"
    assert preview["provider_mode"] == "launch"
    assert preview["mode"] == "review"
    assert preview["launch"] == "not-started"
    assert preview["credential_source"] == "auto"
    assert preview["credential_state"] == "env"
    assert preview["timeout_source"] == "auto"
    assert preview["timeout_applies"] is True
    assert preview["budget_remaining_bytes"] >= 0
    assert preview["budget_remaining_bytes"] == preview["max_bytes"] - preview["bytes"]
    assert len(preview["sha256_packet_md"]) == 64
    assert preview["guarantees"]["leakage"] == "not-guaranteed"
    # 키 값도 패킷 본문도 실리지 않는다.
    assert "x" * 40 not in json.dumps(data)
    assert "print(1)" not in json.dumps(data)
    # 새 표면이므로 질문 본문 부재를 간접 추정에 맡기지 않고 직접 단언한다.
    assert "리뷰해줘" not in json.dumps(data, ensure_ascii=False)


def test_preview_names_a_missing_credential_before_the_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """가장 흔한 20분 낭비가 자격증명 부재다. 그것을 실행 전에 말한다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("PACKET_ASK_GLM_KEY", raising=False)
    monkeypatch.setattr(
        "packet_ask.cli.credential_status",
        lambda provider: type(
            "S", (), {"provider": provider, "environment": "unset",
                      "keychain_item": "missing", "auto_candidate": "missing"}
        )(),
    )
    assert main(_preview(repo, "--json")) == codes.SUCCESS
    preview = json.loads(capsys.readouterr().out)["preview"]
    assert preview["credential_state"] == "missing"


def test_paste_provider_preview_reports_no_credential_requirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """paste 는 벤더를 띄우지 않으므로 자격증명도 deadline 도 없다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    argv = _preview(repo, "--json")
    argv[argv.index("glm")] = "paste"
    assert main(argv) == codes.SUCCESS
    preview = json.loads(capsys.readouterr().out)["preview"]
    assert preview["provider_mode"] == "paste"
    assert preview["credential_state"] == "not-required"
    assert preview["timeout_applies"] is False


def test_preview_and_dry_run_together_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """dry-run 은 패킷 본문을 낸다. 미리보기는 절대 내지 않는다. 조용히 고르지 않는다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    assert main(_preview(repo, "--dry-run")) == codes.USAGE
    captured = capsys.readouterr()
    assert message("preview_dry_run") in captured.err
    assert captured.out == ""


def test_preview_line_is_append_only_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """영수증과 같은 규약이다. 한계 축약도 같이 붙는다."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    assert main(_preview(repo)) == codes.SUCCESS
    line = capsys.readouterr().out.strip()
    assert line.startswith("packet-ask preview provider=glm ")
    assert " launch=not-started " in line
    assert line.endswith(
        " guarantees=leakage:not-guaranteed,cwd_sandbox:none,redaction:denylist"
    )
    assert "\n" not in line
