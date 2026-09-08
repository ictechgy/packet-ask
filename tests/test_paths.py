"""OS 캐시와 신뢰 실행 파일 경로."""

import os
import stat
from pathlib import Path

import pytest

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.paths import (
    TRUSTED_EXECUTABLES,
    packet_cache_dir,
    resolve_trusted_executable,
    trusted_bin_dirs,
    trusted_executable_candidate_exists,
)


def test_packet_cache_dir_uses_override_and_is_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PACKET_ASK_CACHE_DIR 아래 전용 자식만 0700 이고 부모는 건드리지 않는다."""
    cache = tmp_path / "cache"
    cache.mkdir()
    cache.chmod(0o755)
    monkeypatch.setenv("PACKET_ASK_CACHE_DIR", str(cache))
    path = packet_cache_dir()
    assert path == (cache / "packet-ask").resolve()
    assert path.is_dir()
    assert path.stat().st_mode & 0o777 == 0o700
    assert cache.stat().st_mode & 0o777 == 0o755


def test_packet_cache_dir_rejects_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """상대 캐시 경로는 거절한다."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PACKET_ASK_CACHE_DIR", "relative-cache")
    with pytest.raises(PacketAskError):
        packet_cache_dir()


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


def test_packet_cache_dir_rejects_path_under_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """중첩 cwd 에서도 워크트리 안 캐시는 거절한다."""
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    monkeypatch.chdir(src)
    monkeypatch.setenv("PACKET_ASK_CACHE_DIR", str(repo / "cache"))
    with pytest.raises(PacketAskError) as exc:
        packet_cache_dir(worktree=repo.resolve())
    assert exc.value.code == codes.CONFINEMENT
    assert not (repo / "cache" / "packet-ask").exists()


def test_packet_cache_oserror_is_stable_confinement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mkdir/chmod 환경 오류는 traceback 대신 stable confinement가 된다."""
    requested = tmp_path / "cache" / "packet-ask"
    monkeypatch.setenv("PACKET_ASK_CACHE_DIR", str(requested))
    real_mkdir = Path.mkdir

    def fail_target(path: Path, *args: object, **kwargs: object) -> None:
        if path == requested:
            raise OSError("blocked")
        real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_target)
    with pytest.raises(PacketAskError) as exc:
        packet_cache_dir()
    assert exc.value.code == codes.CONFINEMENT
    assert str(requested) not in str(exc.value)


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


@pytest.mark.parametrize("name", TRUSTED_EXECUTABLES)
def test_every_declared_executable_reads_its_own_override(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """선언된 이름마다 override 환경변수가 실제로 읽히는지를 이름별로 본다.

    선언 목록만 단언하면 f-string 이 한 이름에서만 동작해도 녹색이다. 환경변수
    이름은 제품 헬퍼가 아니라 여기서 다시 만들어 형식 자체도 같이 고정한다.
    """
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    # umask 002 에서 기본 mkdir 이 0775 가 되면 검사에 걸려 헛실패한다.
    trusted.chmod(0o755)
    monkeypatch.setattr("packet_ask.paths.trusted_bin_dirs", lambda: [trusted])
    env_name = f"PACKET_ASK_{name.upper()}_BIN"

    monkeypatch.setenv(env_name, name)
    assert resolve_trusted_executable(name) is None

    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    binary = outside / name
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(stat.S_IRWXU)
    monkeypatch.setenv(env_name, str(binary))
    assert resolve_trusted_executable(name) == binary

    # 같은 override 를 존재 진단도 읽는다. doctor 의 help 실패 분기가 쓴다.
    assert trusted_executable_candidate_exists(name) is True

    # 양성 대조: override 를 지우면 같은 파일을 못 찾는다. 신뢰 디렉터리 탐색이
    # 아니라 환경변수가 골랐다는 증거다.
    monkeypatch.delenv(env_name)
    assert resolve_trusted_executable(name) is None
    assert trusted_executable_candidate_exists(name) is False


def test_candidate_existence_skips_owner_and_mode_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """존재 진단은 canonical·소유자·mode 검사를 거치지 않는다.

    doctor 는 `--help` 를 읽지 못했을 때 설치 여부를 이 함수로 말한다. 그래서
    보안 문서는 그 줄을 검사가 아니라 "존재만" 으로 적는다. 검사를 거치는
    resolve 와의 차이를 같은 파일로 대조해서 고정한다.
    """
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    trusted.chmod(0o755)
    loose = tmp_path / "loose"
    loose.mkdir()
    loose.chmod(0o777)
    binary = loose / "kimi"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o777)
    monkeypatch.setattr("packet_ask.paths.trusted_bin_dirs", lambda: [trusted])
    monkeypatch.setenv("PACKET_ASK_KIMI_BIN", str(binary))
    assert trusted_executable_candidate_exists("kimi") is True
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


def test_world_writable_binary_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """그룹·기타 쓰기 가능 실행 파일은 고르지 않는다."""
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    binary = trusted / "kimi"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o777)
    monkeypatch.setattr("packet_ask.paths.trusted_bin_dirs", lambda: [trusted])
    monkeypatch.delenv("PACKET_ASK_KIMI_BIN", raising=False)
    assert resolve_trusted_executable("kimi") is None


def test_group_writable_executable_directory_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실행 파일이 private여도 entry directory를 다른 주체가 바꾸면 거절한다."""
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    trusted.chmod(0o775)
    binary = trusted / "kimi"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    monkeypatch.setattr("packet_ask.paths.trusted_bin_dirs", lambda: [trusted])
    assert resolve_trusted_executable("kimi") is None
    assert trusted_executable_candidate_exists("kimi") is True


def test_trusted_symlink_resolves_to_private_canonical_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Homebrew 형태 symlink는 안전한 target이면 canonical path로 허용한다."""
    trusted = tmp_path / "trusted"
    target_dir = tmp_path / "versions" / "1"
    trusted.mkdir()
    target_dir.mkdir(parents=True)
    trusted.chmod(0o755)
    target_dir.chmod(0o755)
    target = target_dir / "claude"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    target.chmod(0o700)
    (trusted / "claude").symlink_to(target)
    monkeypatch.setattr("packet_ask.paths.trusted_bin_dirs", lambda: [trusted])
    assert resolve_trusted_executable("claude") == target.resolve()


def test_symlink_to_group_writable_target_directory_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """entry dir만 안전하고 canonical target dir이 writable인 우회도 거절한다."""
    trusted = tmp_path / "trusted"
    target_dir = tmp_path / "writable"
    trusted.mkdir()
    target_dir.mkdir()
    trusted.chmod(0o755)
    target_dir.chmod(0o775)
    target = target_dir / "claude"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    target.chmod(0o700)
    (trusted / "claude").symlink_to(target)
    monkeypatch.setattr("packet_ask.paths.trusted_bin_dirs", lambda: [trusted])
    assert resolve_trusted_executable("claude") is None


def test_trusted_bin_dirs_include_local_and_system() -> None:
    """홈브류·시스템·~/.local/bin 은 기본 허용 목록에 있다."""
    dirs = trusted_bin_dirs()
    as_posix = [path.as_posix() for path in dirs]
    assert "/usr/bin" in as_posix or any(item.endswith("/usr/bin") for item in as_posix)
    assert any(path.name == "bin" and path.parent.name == ".local" for path in dirs) or (
        Path.home() / ".local" / "bin" in dirs
    )


def test_minimal_child_env_pins_claude_tmpdirs_inside_isolated_home(
    tmp_path: Path,
) -> None:
    """Claude 자식 tmp 변수가 격리 TMPDIR을 가리킨다.

    변수가 없으면 Claude CLI가 /tmp/claude-UID로 빠져 격리 tmp를 벗어난다.
    감독 실행에서 EPERM으로 재현된 결함이다. 전역 tmp 접근을 넓히지 않는다.
    """
    from packet_ask.paths import minimal_child_env

    env = minimal_child_env(tmp_path)
    assert env["CLAUDE_CODE_TMPDIR"] == env["TMPDIR"]
    assert env["CLAUDE_TMPDIR"] == env["TMPDIR"]
    assert env["TMPDIR"].startswith(str(tmp_path))


def test_confined_hooks_are_empty_by_default(tmp_path: Path) -> None:
    """훅이 없으면 기본 최소 환경 그대로고 상태는 none이다."""
    from packet_ask.paths import (
        confined_hook_state,
        git_subprocess_env,
        minimal_child_env,
    )

    assert confined_hook_state() == "none"
    env = minimal_child_env(tmp_path)
    assert "HTTPS_PROXY" not in env
    assert git_subprocess_env() == {
        "PATH": env["PATH"],
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "LANG": "C",
        "LC_ALL": "C",
    }


def test_confined_child_hook_supplies_proxy_without_parent_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """감독 프록시는 코드 훅으로만 전달되고 부모 클라우드 키는 타지 않는다."""
    from packet_ask.paths import (
        clear_confined_env_hooks,
        confined_hook_state,
        minimal_child_env,
        set_confined_env_hooks,
    )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-secret")
    monkeypatch.setenv("HTTPS_PROXY", "https://parent-proxy.example:8080")
    try:
        set_confined_env_hooks(child=lambda: {"HTTPS_PROXY": "https://proxy.example:8080"})
        assert confined_hook_state() == "external"
        env = minimal_child_env(tmp_path)
        assert env["HTTPS_PROXY"] == "https://proxy.example:8080"
        assert "ANTHROPIC_API_KEY" not in env
        assert "parent-secret" not in env.values()
        assert "parent-proxy.example" not in env.values()
    finally:
        clear_confined_env_hooks()
    assert confined_hook_state() == "none"


def test_confined_hook_passes_only_the_proxy_allowlist(tmp_path: Path) -> None:
    """훅은 프록시 8종만 전달한다. 소유 키·자격증명·로더 주입 키는 떨어진다."""
    from packet_ask.paths import (
        clear_confined_env_hooks,
        minimal_child_env,
        set_confined_env_hooks,
    )

    home = tmp_path / "home"
    try:
        set_confined_env_hooks(
            child=lambda: {
                "HOME": "/elsewhere",
                "PATH": "/elsewhere",
                "TMPDIR": "/elsewhere",
                "CLAUDE_CODE_TMPDIR": "/elsewhere",
                "ANTHROPIC_API_KEY": "hook-secret",
                "ANTHROPIC_BASE_URL": "https://evil.example",
                "LD_PRELOAD": "/elsewhere/evil.so",
                "PYTHONPATH": "/elsewhere",
                "HTTPS_PROXY": "https://proxy.example:8080",
            }
        )
        env = minimal_child_env(home)
        assert env["HOME"] == str(home)
        assert env["TMPDIR"].startswith(str(home))
        assert env["CLAUDE_CODE_TMPDIR"] == env["TMPDIR"]
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_BASE_URL" not in env
        assert "LD_PRELOAD" not in env
        assert "PYTHONPATH" not in env
        assert "hook-secret" not in env.values()
        assert env["HTTPS_PROXY"] == "https://proxy.example:8080"
    finally:
        clear_confined_env_hooks()


def test_empty_hook_still_reports_external(tmp_path: Path) -> None:
    """상태는 등록 여부지 효과가 아니다. 빈 훅도 external이다.

    효과가 있어야 external이라고 쓰면 상수가 기전보다 오래 살아남는다.
    과대 표기 방향이라 문서에 정직하게 고정한다.
    """
    from packet_ask.paths import (
        clear_confined_env_hooks,
        confined_hook_state,
        minimal_child_env,
        set_confined_env_hooks,
    )

    try:
        set_confined_env_hooks(child=lambda: {})
        assert confined_hook_state() == "external"
        assert "HTTPS_PROXY" not in minimal_child_env(tmp_path)
    finally:
        clear_confined_env_hooks()


def test_confined_git_hook_supplies_extra_without_git_config_override() -> None:
    """git 훅은 DEVELOPER_DIR만 주고 전역 설정 핀을 풀지 못한다."""
    from packet_ask.paths import (
        clear_confined_env_hooks,
        git_subprocess_env,
        set_confined_env_hooks,
    )

    try:
        set_confined_env_hooks(
            git=lambda: {
                "DEVELOPER_DIR": "/Library/Developer/CommandLineTools",
                "HOME": "/elsewhere",
                "ANTHROPIC_API_KEY": "hook-secret",
                "GIT_CONFIG_GLOBAL": "/elsewhere",
                "PATH": "/elsewhere",
            }
        )
        env = git_subprocess_env()
        assert env["DEVELOPER_DIR"] == "/Library/Developer/CommandLineTools"
        assert "HOME" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull
        assert env["GIT_CONFIG_SYSTEM"] == os.devnull
        assert env["PATH"] != "/elsewhere"
    finally:
        clear_confined_env_hooks()


def test_confined_hook_failure_stops_before_launch(tmp_path: Path) -> None:
    """훅 실패는 격리 초기화 실패다. 이유를 보고하고 벤더를 실행하지 않는다."""
    from packet_ask.paths import (
        clear_confined_env_hooks,
        minimal_child_env,
        set_confined_env_hooks,
    )
    from packet_ask.text import message

    def _broken() -> dict[str, str]:
        raise RuntimeError("hook broken")

    try:
        set_confined_env_hooks(child=_broken)
        with pytest.raises(PacketAskError) as excinfo:
            minimal_child_env(tmp_path)
        assert excinfo.value.code == codes.CONFINEMENT
        assert str(excinfo.value) == message("confined_hook_failed")
    finally:
        clear_confined_env_hooks()
