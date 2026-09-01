"""OS 캐시와 신뢰 실행 파일 경로. cwd와 전체 PATH는 신뢰하지 않는다."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.text import message


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
    env = {
        "HOME": str(home),
        "PATH": trusted_path_value(),
        "LANG": os.environ.get("LANG", "C"),
        "LC_ALL": os.environ.get("LC_ALL", "C"),
        "TMPDIR": str(tmp),
    }
    if extra:
        env.update(extra)
    return env


def git_subprocess_env() -> dict[str, str]:
    """git 훅·글로벌 설정·부모 클라우드 키를 타지 않는 최소 환경."""
    return {
        "PATH": trusted_path_value(),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "LANG": "C",
        "LC_ALL": "C",
    }


def trusted_path_value() -> str:
    """자식 프로세스에 줄 PATH. 허용 디렉터리만 포함한다."""
    return os.pathsep.join(str(path) for path in trusted_bin_dirs()) or "/usr/bin:/bin"


def resolve_trusted_executable(name: str) -> Path | None:
    """허용된 디렉터리에서만 실행 파일을 찾는다."""
    override = os.environ.get(f"PACKET_ASK_{name.upper()}_BIN", "").strip()
    if override:
        return _executable_if_valid(Path(override))
    for directory in trusted_bin_dirs():
        found = _executable_if_valid(directory / name)
        if found is not None:
            return found
    return None


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
