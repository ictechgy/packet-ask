"""OS 캐시와 신뢰 실행 파일 경로. cwd와 전체 PATH는 신뢰하지 않는다."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


def packet_cache_dir() -> Path:
    """패킷 임시 디렉터리의 부모. 워크트리 밖 OS 캐시다."""
    raw = os.environ.get("PACKET_ASK_CACHE_DIR", "").strip()
    path = Path(raw) if raw else _default_cache_dir()
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(stat.S_IRWXU)
    return path.resolve()


def _default_cache_dir() -> Path:
    """플랫폼 캐시 루트 아래 packet-ask 를 쓴다."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "packet-ask"
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        return Path(xdg) / "packet-ask"
    return Path.home() / ".cache" / "packet-ask"


def trusted_bin_dirs() -> list[Path]:
    """공식 CLI를 찾을 디렉터리. 사용자 PATH 전체를 쓰지 않는다."""
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
    """절대경로이고 실행 가능한 파일만 반환한다. 상대경로는 거절한다."""
    if not path.is_absolute() or not path.exists() or path.is_dir():
        return None
    if not os.access(path, os.X_OK):
        return None
    return path
