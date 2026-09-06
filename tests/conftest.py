"""테스트가 실제 사용자 providers.toml 을 읽지 않게 한다."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_user_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """기본 overlay 경로를 빈 파일로 돌린다."""
    monkeypatch.setenv("PACKET_ASK_PROVIDERS_FILE", str(tmp_path / "missing-providers.toml"))


@pytest.fixture(autouse=True)
def clear_confined_env_hooks() -> None:
    """감독 제한 환경 훅이 테스트 사이에 남지 않게 한다."""
    yield
    try:
        from packet_ask.paths import clear_confined_env_hooks as clear
    except ImportError:
        return
    clear()
