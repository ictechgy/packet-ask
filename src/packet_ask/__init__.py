"""개인 코딩 구독에 패킷만 보내는 CLI."""

from packet_ask.cli import main as run_cli


def main() -> None:
    """콘솔 스크립트 진입점. 종료 코드를 프로세스로 전달한다."""
    raise SystemExit(run_cli())


__all__ = ["main", "run_cli"]
