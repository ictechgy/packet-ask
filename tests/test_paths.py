"""OS 캐시와 신뢰 실행 파일 경로."""

import os
import stat
from pathlib import Path

import pytest

from packet_ask.paths import packet_cache_dir, resolve_trusted_executable, trusted_bin_dirs


def test_packet_cache_dir_uses_override_and_is_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PACKET_ASK_CACHE_DIR 이 있으면 그곳을 쓰고 0700 이다."""
    cache = tmp_path / "cache"
    monkeypatch.setenv("PACKET_ASK_CACHE_DIR", str(cache))
    path = packet_cache_dir()
    assert path == cache.resolve()
    assert path.is_dir()
    assert path.stat().st_mode & 0o777 == 0o700


def test_packet_cache_dir_is_not_under_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cwd 아래 .packet-ask-tmp 를 쓰지 않는다. cwd는 샌드박스가 아니다."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PACKET_ASK_CACHE_DIR", raising=False)
    path = packet_cache_dir()
    assert path.name != ".packet-ask-tmp"
    with pytest.raises(ValueError):
        path.resolve().relative_to(tmp_path.resolve())


def test_trusted_executable_ignores_untrusted_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """전체 PATH 앞쪽의 래퍼는 고르지 않는다."""
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    binary = trusted / "kimi"
    binary.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    binary.chmod(stat.S_IRWXU)
    untrusted = tmp_path / "untrusted"
    untrusted.mkdir()
    wrapper = untrusted / "kimi"
    wrapper.write_text("#!/bin/sh\necho WRAPPER\n", encoding="utf-8")
    wrapper.chmod(stat.S_IRWXU)
    monkeypatch.setenv("PATH", str(untrusted) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr("packet_ask.paths.trusted_bin_dirs", lambda: [trusted])
    found = resolve_trusted_executable("kimi")
    assert found == binary
    assert found != wrapper


def test_trusted_executable_missing_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """허용 디렉터리에 없으면 None 이다. PATH에 있어도 무시한다."""
    empty = tmp_path / "empty"
    empty.mkdir()
    untrusted = tmp_path / "bin"
    untrusted.mkdir()
    fake = untrusted / "claude"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(stat.S_IRWXU)
    monkeypatch.setenv("PATH", str(untrusted))
    monkeypatch.setattr("packet_ask.paths.trusted_bin_dirs", lambda: [empty])
    monkeypatch.delenv("PACKET_ASK_CLAUDE_BIN", raising=False)
    assert resolve_trusted_executable("claude") is None


def test_trusted_bin_override_must_be_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PACKET_ASK_*_BIN 은 절대경로만 받는다."""
    monkeypatch.setenv("PACKET_ASK_KIMI_BIN", "kimi")
    monkeypatch.setattr("packet_ask.paths.trusted_bin_dirs", lambda: [tmp_path])
    assert resolve_trusted_executable("kimi") is None


def test_packet_ask_bin_dirs_ignores_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PACKET_ASK_BIN_DIRS 상대경로는 신뢰 목록에 넣지 않는다."""
    monkeypatch.setenv("PACKET_ASK_BIN_DIRS", "relative/bin")
    dirs = trusted_bin_dirs()
    assert all(path.is_absolute() for path in dirs)
    assert not any(path.as_posix().endswith("relative/bin") for path in dirs)


def test_packet_ask_bin_dirs_accepts_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PACKET_ASK_BIN_DIRS 절대경로는 앞에 붙는다."""
    extra = tmp_path / "official"
    extra.mkdir()
    monkeypatch.setenv("PACKET_ASK_BIN_DIRS", str(extra))
    dirs = trusted_bin_dirs()
    assert extra in dirs


def test_trusted_bin_dirs_include_local_and_system() -> None:
    """홈브류·시스템·~/.local/bin 은 기본 허용 목록에 있다."""
    dirs = trusted_bin_dirs()
    as_posix = [path.as_posix() for path in dirs]
    assert "/usr/bin" in as_posix or any(item.endswith("/usr/bin") for item in as_posix)
    assert any(path.name == "bin" and path.parent.name == ".local" for path in dirs) or (
        Path.home() / ".local" / "bin" in dirs
    )
