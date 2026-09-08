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

# 보고에 실을 수 있는 사유 메시지 키. 홈별 실패를 모으려면 사유가 언어와
# 무관해야 하므로 렌더된 문장이 아니라 키를 나른다. 새 실패 종류는 여기에
# 등록해야 보고되고, 등록하지 않으면 `_SkillGuardError` 가 거절한다.
REASON_KEYS = frozenset(
    {
        "skill_symlink",
        "skill_exists",
        "skill_read_failed",
        "skill_write_failed",
    }
)


@dataclass(frozen=True)
class SkillInstallFailure:
    """홈 하나에서 설치하지 못한 결과.

    `relative` 는 사용자 입력이 아니라 `SKILL_RELATIVE_PATHS` 카탈로그 값이다.
    `reason_key` 는 렌더된 문장이 아니라 메시지 카탈로그 키다. 문장을 담으면
    실패 시점의 언어 설정이 보고에 굳고, 미래의 메시지가 경로를 보간하기
    시작해도 보고서 쪽에서 막을 수 없다. 키를 나르면 출력 시점에 렌더되고
    허용된 사유 집합을 테스트로 고정할 수 있다.
    """

    relative: str
    code: int
    reason_key: str


@dataclass(frozen=True)
class SkillInstallReport:
    """설치 결과와 실패 목록.

    홈 사이는 독립적이다. 한 홈의 성공·실패가 다른 홈의 시도 자체를 막지
    않는다. 홈 **안**에서의 원자성은 주장하지 않는다 — 쓰기는
    truncate-and-write 다.
    """

    written: tuple[Path, ...]
    failures: tuple[SkillInstallFailure, ...]


class _SkillGuardError(PacketAskError):
    """사유 메시지 키를 함께 나르는 설치 가드 실패.

    홈 단위로 모아 보고하려면 실패가 왜 났는지를 언어와 무관하게 실어야 한다.
    `PacketAskError` 는 렌더된 문장만 나르므로 키를 잃는다.
    """

    def __init__(self, reason_key: str, code: int) -> None:
        if reason_key not in REASON_KEYS:
            # 새 실패 종류를 사유 없이 보고하지 못하게 한다. 보고에 실리는
            # 문장은 카탈로그에 등록되고 테스트가 고정하는 것만 허용한다.
            raise KeyError(reason_key)
        super().__init__(message(reason_key), code)
        self.reason_key = reason_key


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
    SKILL.md 는 `force` 없이 덮지 않는다. 읽거나 쓰지 못한 경우(OSError,
    non-UTF-8 기존 파일)도 홈 단위 가드 실패로 바꾼다. 바꾸지 않으면 그
    예외가 그대로 새어 나와 트레이스백이 되고 나머지 홈은 시도되지 않아
    고치려던 그 상태가 다시 난다. 실측으로 두 경로 모두 재현했다.

    모으는 실패는 `_SkillGuardError` 로 한정한다. 그 밖의 예외는 예상하지
    못한 것이므로 조용히 실패 목록에 넣지 않고 그대로 던진다.
    """
    root = home if home is not None else Path.home()
    body = skill_markdown()
    written: list[Path] = []
    failures: list[SkillInstallFailure] = []
    for relative in SKILL_RELATIVE_PATHS:
        try:
            _install_one(root, Path(relative), body, force)
        except _SkillGuardError as exc:
            failures.append(SkillInstallFailure(relative, exc.code, exc.reason_key))
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
            raise _SkillGuardError("skill_symlink", codes.CONFINEMENT)


def _write_skill(path: Path, body: str, force: bool) -> None:
    """심링크가 아닌 경로에만 쓰고, 다른 내용은 force 없이 덮지 않는다."""
    if path.exists() or path.is_symlink():
        _replace_existing_skill(path, body, force)
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _SkillGuardError("skill_write_failed", codes.CONFINEMENT) from exc
    if path.parent.is_symlink():
        raise _SkillGuardError("skill_symlink", codes.CONFINEMENT)
    _write_body(path, body)


def _replace_existing_skill(path: Path, body: str, force: bool) -> None:
    """기존 파일이 패키지 원문과 같으면 두고, 다르면 force 만 허용한다."""
    if path.is_symlink():
        raise _SkillGuardError("skill_symlink", codes.CONFINEMENT)
    try:
        existing = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # 읽지 못하면 패키지 원문과 같다는 것을 증명할 수 없으므로 덮지 않는다.
        # 이 분기는 force 를 보기 전에 온다 — `--force` 로도 풀리지 않는다.
        # non-UTF-8 로 저장한 사용자 SKILL.md 가 실제로 이 경로를 탄다. 이전에는
        # UnicodeDecodeError 가 그대로 새어 나와 트레이스백이 되고 나머지 홈은
        # 시도되지 않았다.
        raise _SkillGuardError("skill_read_failed", codes.CONFINEMENT) from exc
    if existing == body:
        return
    if not force:
        raise _SkillGuardError("skill_exists", codes.USAGE)
    _write_body(path, body)


def _write_body(path: Path, body: str) -> None:
    """본문을 쓴다. 실패는 경로 없는 고정 문장으로 바꾼다.

    truncate-and-write 다. temp+rename 이 아니므로 쓰기 중에 죽으면 잘린
    SKILL.md 가 남고, 다음 실행은 그것을 다른 내용으로 봐 `--force` 없이
    덮지 않는다. 홈 하나 안에서의 원자성은 주장하지 않는다.
    """
    try:
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        raise _SkillGuardError("skill_write_failed", codes.CONFINEMENT) from exc
