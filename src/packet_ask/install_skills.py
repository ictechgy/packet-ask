"""Claude / Codex / Grok 사용자 스킬 디렉터리에 SKILL.md 를 설치한다."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from packet_ask import codes
from packet_ask.errors import PacketAskError
from packet_ask.text import message

SKILL_RELATIVE_PATHS = (
    ".claude/skills/packet-ask/SKILL.md",
    ".grok/skills/packet-ask/SKILL.md",
    ".codex/skills/packet-ask/SKILL.md",
    ".agents/skills/packet-ask/SKILL.md",
)


@dataclass(frozen=True)
class SkillInstallFailure:
    """홈 하나에서 설치하지 못한 결과.

    `relative` 는 사용자 입력이 아니라 `SKILL_RELATIVE_PATHS` 카탈로그 값이다.
    `reason` 은 그 실패에서 나온 고정 카탈로그 문장이라 경로·키를 담지 않는다.
    """

    relative: str
    code: int
    reason: str


@dataclass(frozen=True)
class SkillInstallReport:
    """쓴 경로와 실패 목록. 한 홈의 실패가 나머지를 막지 않는다."""

    written: tuple[Path, ...]
    failures: tuple[SkillInstallFailure, ...]


def skill_markdown() -> str:
    """패키지에 실은 스킬 원문을 읽는다."""
    return files("packet_ask.data").joinpath("SKILL.md").read_text(encoding="utf-8")


def install_skills(home: Path | None = None, force: bool = False) -> SkillInstallReport:
    """각 하니스 홈을 독립적으로 시도하고 결과를 모은다.

    한 홈이 깨졌다고 나머지를 막지 않는다. 이전 동작은 첫 실패에서 예외를
    던졌는데, 그 시점까지 쓴 홈은 **이미 쓰여 있고** 반환 목록은 통째로
    사라져 CLI 가 한 줄도 보고하지 못했다. 실측: 두 번째 홈이 심링크면 첫
    홈은 쓰인 채 exit 13, 보고 0줄, 나머지 두 홈은 낡은 SKILL.md. 문제는
    부분 설치가 아니라 보고 없는 부분 설치였다.

    가드는 그대로다. 심링크 구성요소는 홈 단위로 거절되고 다른 내용의
    SKILL.md 는 `force` 없이 덮지 않는다. 홈 하나가 실패해도 나머지를
    진행하고 실패를 전부 모은다.
    """
    root = home if home is not None else Path.home()
    body = skill_markdown()
    written: list[Path] = []
    failures: list[SkillInstallFailure] = []
    for relative in SKILL_RELATIVE_PATHS:
        try:
            _install_one(root, Path(relative), body, force)
        except PacketAskError as exc:
            failures.append(SkillInstallFailure(relative, exc.code, str(exc)))
            continue
        written.append(root / relative)
    return SkillInstallReport(tuple(written), tuple(failures))


def install_exit_code(failures: tuple[SkillInstallFailure, ...]) -> int:
    """실패 목록에서 종료 코드를 고른다.

    격리 검사(confinement)가 사용 오류(usage)보다 무겁다. 심링크 홈과
    `--force` 없는 홈이 함께 있어도 13 을 반환한다 — `--force` 를 더해도
    해결되지 않는 실패가 남아 있다는 뜻이기 때문이다. 그 밖에는 첫 실패의
    코드를 그대로 쓴다. 새 실패 종류가 usage 로 뭉개지지 않게 하려고
    코드를 매핑하지 않는다.
    """
    if any(item.code == codes.CONFINEMENT for item in failures):
        return codes.CONFINEMENT
    if failures:
        return failures[0].code
    return codes.SUCCESS


def _install_one(root: Path, relative: Path, body: str, force: bool) -> None:
    """홈 하나에 쓴다. 가드는 여기서 전부 유지된다."""
    _reject_symlink_components(root, relative)
    _write_skill(root / relative, body, force)


def _reject_symlink_components(root: Path, relative: Path) -> None:
    """선택한 하니스 홈 아래 기존 경로 조각의 심링크를 모두 거절한다."""
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PacketAskError(message("skill_symlink"), codes.CONFINEMENT)


def _write_skill(path: Path, body: str, force: bool) -> None:
    """심링크가 아닌 경로에만 쓰고, 다른 내용은 force 없이 덮지 않는다."""
    if path.exists() or path.is_symlink():
        _replace_existing_skill(path, body, force)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise PacketAskError(message("skill_symlink"), codes.CONFINEMENT)
    path.write_text(body, encoding="utf-8")


def _replace_existing_skill(path: Path, body: str, force: bool) -> None:
    """기존 파일이 패키지 원문과 같으면 두고, 다르면 force 만 허용한다."""
    if path.is_symlink():
        raise PacketAskError(message("skill_symlink"), codes.CONFINEMENT)
    existing = path.read_text(encoding="utf-8")
    if existing == body:
        return
    if not force:
        raise PacketAskError(message("skill_exists"), codes.USAGE)
    path.write_text(body, encoding="utf-8")
