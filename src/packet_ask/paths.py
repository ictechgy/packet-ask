"""OS 캐시와 신뢰 실행 파일 경로. cwd와 전체 PATH는 신뢰하지 않는다."""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.text import message

# 감독 프로세스가 코드로만 등록하는 제한 환경 훅이다. 사용자 TOML·CLI·환경
# 변수 표면이 아니므로 거절 목록을 열지 않는다. 외부 보호 실행기가 함수 참조
# 여러 곳을 monkeypatch하는 대신 이 한 점만 거치도록 둔다. 제품 버전이
# 바뀌면 외부 어댑터의 버전 확인과 함께 재검증해야 한다.
_CONFINED_CHILD_HOOK: Callable[[], dict[str, str]] | None = None
_CONFINED_GIT_HOOK: Callable[[], dict[str, str]] | None = None

_SUPERVISION_NONE = "none"
_SUPERVISION_EXTERNAL = "external"

# 훅이 줄 수 있는 이름은 등록된 것만 받는다. denylist는 로더 주입 계열
# 키(LD_PRELOAD·PYTHONPATH 등)를 빠뜨리면 격리가 약해지므로, 필요한 이름만
# allowlist로 연다. 자식은 감독 프록시 8종, git은 DEVELOPER_DIR만이다.
# 값 검증은 하지 않는다. 훅은 in-process 코드라 값의 책임은 호출자에게 있다.
_CHILD_HOOK_ALLOWED = frozenset(
    {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }
)
_GIT_HOOK_ALLOWED = frozenset({"DEVELOPER_DIR"})


def set_confined_env_hooks(
    child: Callable[[], dict[str, str]] | None = None,
    git: Callable[[], dict[str, str]] | None = None,
) -> None:
    """감독 제한 환경 훅을 등록한다. None이면 해당 슬롯을 비운다."""
    global _CONFINED_CHILD_HOOK, _CONFINED_GIT_HOOK
    _CONFINED_CHILD_HOOK = child
    _CONFINED_GIT_HOOK = git


def clear_confined_env_hooks() -> None:
    """등록된 훅을 모두 비운다. 테스트 격리용이기도 하다."""
    set_confined_env_hooks()


def confined_hook_state() -> str:
    """훅 등록 여부만 말한다. OS 격리 검증 결과가 아니다."""
    if _CONFINED_CHILD_HOOK is None and _CONFINED_GIT_HOOK is None:
        return _SUPERVISION_NONE
    return _SUPERVISION_EXTERNAL


def packet_cache_dir(worktree: Path | None = None) -> Path:
    """패킷 임시 디렉터리의 부모. 워크트리 밖 전용 캐시다."""
    dedicated = _requested_cache_dir()
    _reject_cache_inside_tree(dedicated, worktree)
    _ensure_private_dir(dedicated)
    resolved = dedicated.resolve()
    _reject_cache_inside_tree(resolved, worktree)
    return resolved


def _requested_cache_dir() -> Path:
    """환경변수 또는 플랫폼 기본 캐시 경로를 고른다. 아직 만들지 않는다."""
    raw = os.environ.get("PACKET_ASK_CACHE_DIR", "").strip()
    if not raw:
        return _default_cache_dir()
    specified = Path(raw)
    if not specified.is_absolute():
        raise PacketAskError(message("cache_absolute"), codes.CONFINEMENT)
    if specified.name == "packet-ask":
        return specified
    return specified / "packet-ask"


def _reject_cache_inside_tree(path: Path, worktree: Path | None) -> None:
    """cwd 또는 git 워크트리 안의 캐시를 거절한다."""
    if _is_under(path, Path.cwd().resolve()):
        raise PacketAskError(message("cache_cwd"), codes.CONFINEMENT)
    if worktree is None:
        return
    if _is_under(path, worktree.resolve()):
        raise PacketAskError(message("cache_worktree"), codes.CONFINEMENT)


def _is_under(path: Path, parent: Path) -> bool:
    """path 가 parent 아래인지 본다."""
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _ensure_private_dir(path: Path) -> None:
    """전용 디렉터리만 만들고 0700 으로 잠근다. 심링크는 거절한다."""
    try:
        if path.exists() and path.is_symlink():
            raise PacketAskError(message("cache_symlink"), codes.CONFINEMENT)
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise PacketAskError(message("cache_symlink"), codes.CONFINEMENT)
        info = path.stat()
        if info.st_uid not in {0, os.getuid()}:
            raise PacketAskError(message("cache_owner"), codes.CONFINEMENT)
        path.chmod(stat.S_IRWXU)
    except PacketAskError:
        raise
    except OSError as exc:
        raise PacketAskError(message("cache_invalid"), codes.CONFINEMENT) from exc


def _default_cache_dir() -> Path:
    """플랫폼 캐시 루트 아래 packet-ask 를 쓴다."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "packet-ask"
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        return Path(xdg) / "packet-ask"
    return Path.home() / ".cache" / "packet-ask"


def trusted_bin_dirs() -> list[Path]:
    """사용자가 신뢰한다고 지정한 실행 파일 디렉터리. PATH 전체를 쓰지 않는다."""
    extras = []
    for item in os.environ.get("PACKET_ASK_BIN_DIRS", "").split(os.pathsep):
        raw = item.strip()
        if not raw:
            continue
        path = Path(raw)
        if path.is_absolute():
            extras.append(path)
    return extras + [
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/usr/bin"),
        Path("/bin"),
        Path.home() / ".local" / "bin",
    ]


def minimal_child_env(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """부모 클라우드 키를 복사하지 않는 최소 환경."""
    tmp = home / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    tmp_text = str(tmp)
    env = {
        "HOME": str(home),
        "PATH": trusted_path_value(),
        "LANG": os.environ.get("LANG", "C"),
        "LC_ALL": os.environ.get("LC_ALL", "C"),
        "TMPDIR": tmp_text,
        # Claude CLI는 전용 tmp 변수가 없으면 /tmp/claude-UID로 빠져 격리
        # tmp를 벗어난다. 감독 실행에서 EPERM으로 재현됐다. 전역 tmp를 열지
        # 않고 격리 tmp를 가리킨다.
        "CLAUDE_CODE_TMPDIR": tmp_text,
        "CLAUDE_TMPDIR": tmp_text,
    }
    env.update(_child_hook_extra())
    if extra:
        env.update(extra)
    return env


def git_subprocess_env() -> dict[str, str]:
    """git 훅·글로벌 설정·부모 클라우드 키를 타지 않는 최소 환경."""
    env = {
        "PATH": trusted_path_value(),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "LANG": "C",
        "LC_ALL": "C",
    }
    env.update(_git_hook_extra())
    return env


def _child_hook_extra() -> dict[str, str]:
    """등록된 자식 훅의 허용값만 돌려준다. 없으면 빈 dict다."""
    if _CONFINED_CHILD_HOOK is None:
        return {}
    return _run_confined_hook(_CONFINED_CHILD_HOOK, allowed=_CHILD_HOOK_ALLOWED)


def _git_hook_extra() -> dict[str, str]:
    """등록된 git 훅의 허용값만 돌려준다. 없으면 빈 dict다."""
    if _CONFINED_GIT_HOOK is None:
        return {}
    return _run_confined_hook(_CONFINED_GIT_HOOK, allowed=_GIT_HOOK_ALLOWED)


def _run_confined_hook(
    hook: Callable[[], dict[str, str]],
    *,
    allowed: frozenset[str],
) -> dict[str, str]:
    """훅을 실행하고 허용된 str 쌍만 남긴다. 실패하면 벤더를 실행하지 않는다."""
    try:
        produced = hook()
    except Exception as exc:
        raise PacketAskError(message("confined_hook_failed"), codes.CONFINEMENT) from exc
    if not isinstance(produced, dict):
        raise PacketAskError(message("confined_hook_failed"), codes.CONFINEMENT)
    cleaned: dict[str, str] = {}
    for key, value in produced.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise PacketAskError(message("confined_hook_failed"), codes.CONFINEMENT)
        if key in allowed:
            cleaned[key] = value
    return cleaned


def trusted_path_value() -> str:
    """자식 프로세스에 줄 PATH. 허용 디렉터리만 포함한다."""
    return os.pathsep.join(str(path) for path in trusted_bin_dirs()) or "/usr/bin:/bin"


# override 환경변수(`PACKET_ASK_<NAME>_BIN`)가 존재하는 신뢰 실행 파일 이름.
# 이 이름은 f-string 으로 만들어지므로 소스 스캔으로는 열거되지 않는다. 문서
# parity 테스트가 이 선언을 유일한 출처로 쓰고, 같은 테스트가 선언이 실제
# 호출 지점과 레지스트리를 덮는지 검사한다. 선언 없이 이름을 추가하면 그
# 변수는 어떤 문서에도 나타나지 않는다 — `PACKET_ASK_GIT_BIN` 과 paste 전용
# `PACKET_ASK_GROK_BIN`·`PACKET_ASK_AGY_BIN` 이 실제로 두 SECURITY 문서와
# env.example 어디에도 없이 동작하고 있었다.
# grok·agy 는 paste 전용이라 override 가 doctor 의 installed 판정만 바꾼다.
TRUSTED_EXECUTABLES: tuple[str, ...] = ("claude", "kimi", "git", "grok", "agy")


def _trusted_executable_override_env(name: str) -> str:
    """override 환경변수 이름을 만든다. 코드와 문서 대조가 같은 함수를 쓴다."""
    return f"PACKET_ASK_{name.upper()}_BIN"


def trusted_executable_override_envs() -> tuple[str, ...]:
    """선언된 override 환경변수 이름 전부. 문서 대조의 유일한 출처다."""
    return tuple(_trusted_executable_override_env(name) for name in TRUSTED_EXECUTABLES)


def resolve_trusted_executable(name: str) -> Path | None:
    """허용된 디렉터리에서만 실행 파일을 찾는다."""
    override = os.environ.get(_trusted_executable_override_env(name), "").strip()
    if override:
        return _executable_if_valid(Path(override))
    for directory in trusted_bin_dirs():
        found = _executable_if_valid(directory / name)
        if found is not None:
            return found
    return None


def trusted_executable_candidate_exists(name: str) -> bool:
    """경로를 공개하지 않고 allowlist entry 존재만 진단한다."""
    override = os.environ.get(_trusted_executable_override_env(name), "").strip()
    candidates = [Path(override)] if override and Path(override).is_absolute() else []
    if not candidates:
        candidates = [directory / name for directory in trusted_bin_dirs()]
    for candidate in candidates:
        try:
            if os.path.lexists(candidate):
                return True
        except OSError:
            continue
    return False


def _executable_if_valid(path: Path) -> Path | None:
    """신뢰 디렉터리의 canonical, private executable만 반환한다."""
    if not path.is_absolute() or not _trusted_executable_directory(path.parent):
        return None
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except OSError:
        return None
    if not _trusted_executable_directory(resolved.parent):
        return None
    if not stat.S_ISREG(info.st_mode) or not os.access(resolved, os.X_OK):
        return None
    if info.st_uid not in {0, os.getuid()}:
        return None
    if stat.S_IMODE(info.st_mode) & 0o022:
        return None
    return resolved


def _trusted_executable_directory(path: Path) -> bool:
    """entry/target를 바꿀 수 있는 immediate directory 권한을 검사한다."""
    try:
        info = path.stat()
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}:
        return False
    return not bool(stat.S_IMODE(info.st_mode) & 0o022)
