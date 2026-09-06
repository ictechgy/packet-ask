"""선택형 보호 실행 표시: 보장 선언 없이 적용 상태만 구분한다.

`supervision` 은 "OS 샌드박스다"라는 주장이 아니다. 호스트 감독 프로세스가
코드 훅으로 제한 환경을 더했는지(`external`) 아닌지(`none`)만 말한다.
`sandbox: none` 상수는 그대로 두고, 실행기 존재만으로 보장을 선언하지 않는다.
"""

from pathlib import Path

from packet_ask.packet import Packet
from packet_ask.redact import RedactionReport


def _packet(tmp_path: Path) -> Packet:
    """영수증 단위 테스트용 최소 packet."""
    return Packet(root=tmp_path, report=RedactionReport(), _packet_text="hello")


def _receipt_kwargs(packet: Packet) -> dict:
    """build_receipt 필수 인자."""
    return {
        "provider": "glm",
        "selector": "files",
        "files": [],
        "diff_text": None,
        "packet": packet,
        "timeout_seconds": 1200,
        "timeout_source": "auto",
        "timeout_applies": True,
        "surface": "absent",
        "effort": None,
        "effort_source": "vendor-default",
    }


def test_receipt_supervision_defaults_to_none(tmp_path: Path) -> None:
    """훅이 없으면 supervision은 none이고 사람 줄에도 토큰이 붙는다."""
    from packet_ask.paths import confined_hook_state
    from packet_ask.receipt import build_receipt, format_receipt_line

    assert confined_hook_state() == "none"
    receipt = build_receipt(**_receipt_kwargs(_packet(tmp_path)))
    assert receipt["supervision"] == "none"
    assert " supervision=none" in format_receipt_line(receipt)


def test_receipt_supervision_follows_the_hook(tmp_path: Path) -> None:
    """훅이 등록되면 receipt가 external을 싣는다. 효과 검증이 아니라 결로다.

    프록시가 자식 환경에 실제로 닿는 것까지 함께 고정한다. 다만 빈 훅도
    external이므로, 상태는 영수증 빌드 시점의 등록 여부지 실행 감사가 아니다.
    """
    from packet_ask.paths import (
        clear_confined_env_hooks,
        minimal_child_env,
        set_confined_env_hooks,
    )
    from packet_ask.receipt import build_receipt

    try:
        set_confined_env_hooks(child=lambda: {"HTTPS_PROXY": "https://proxy.example:8080"})
        assert minimal_child_env(tmp_path)["HTTPS_PROXY"] == "https://proxy.example:8080"
        receipt = build_receipt(**_receipt_kwargs(_packet(tmp_path)))
        assert receipt["supervision"] == "external"
    finally:
        clear_confined_env_hooks()
    assert build_receipt(**_receipt_kwargs(_packet(tmp_path)))["supervision"] == "none"


def test_summary_and_preview_carry_supervision(tmp_path: Path) -> None:
    """inspect 요약과 preview도 같은 상태를 싣는다."""
    from packet_ask.receipt import (
        build_packet_summary,
        build_preview,
        format_preview_line,
    )

    packet = _packet(tmp_path)
    summary = build_packet_summary("review", "files", [], None, packet, surface="absent")
    assert summary["supervision"] == "none"
    preview = build_preview(
        {
            **_receipt_preview_base(packet),
            "supervision": summary["supervision"],
        },
        mode="review",
        provider_mode="launch",
        credential_source="auto",
        credential_state="env",
        max_bytes=262144,
    )
    assert preview["supervision"] == "none"
    assert " supervision=none" in format_preview_line(preview)


def _receipt_preview_base(packet: Packet) -> dict:
    """preview가 요구하는 영수증 최소 필드."""
    from packet_ask.receipt import build_receipt

    return build_receipt(
        provider="glm",
        selector="files",
        files=[],
        diff_text=None,
        packet=packet,
        timeout_seconds=1200,
        timeout_source="auto",
        timeout_applies=True,
        surface="absent",
        effort=None,
        effort_source="vendor-default",
    )


def test_ledger_copies_supervision_only_when_present() -> None:
    """대장은 이름으로 골라 복사한다. 없으면 만들지 않는다."""
    from packet_ask.ledger import build_ledger_entry

    base = {
        "provider": "glm",
        "selector": "files",
        "paths": ["a.py"],
        "bytes": 10,
        "sha256_packet_md": "a" * 64,
        "redaction": {"emails": 1},
    }
    assert "supervision" not in build_ledger_entry("review", dict(base))
    entry = build_ledger_entry("review", {**base, "supervision": "external"})
    assert entry["supervision"] == "external"


def test_doctor_supervision_line_precedes_signals(
    capsys,
) -> None:
    """보호 표시는 signals 줄 앞에 둔다. 상쇄가 마지막에 와야 한다."""
    from packet_ask.cli import main
    from packet_ask.doctor import format_doctor_signals_line, format_supervision_line

    assert format_supervision_line() == "packet-ask supervision state=none"
    assert main(["doctor"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == format_doctor_signals_line()
    assert lines[-2] == format_supervision_line()
    assert len(lines) > 2
