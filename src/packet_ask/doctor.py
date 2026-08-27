"""설치된 공식 CLI가 무도구 원샷을 지원하는지 검사한다."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderStatus:
    """프로바이더별 실행 가능 여부. 비밀 값은 담지 않는다."""

    name: str
    installed: bool
    can_launch: bool
    note: str


def claude_supports_isolated_print(help_text: str) -> bool:
    """Claude Code help에 격리 원샷에 필요한 플래그가 있는지 본다."""
    needed = ("--bare", "--tools", "--no-session-persistence", "--setting-sources")
    return all(flag in help_text for flag in needed)


def kimi_supports_print(help_text: str) -> bool:
    """Kimi help에 print/prompt 원샷이 있는지 본다. 도구 차단은 별도."""
    return "-p" in help_text or "--prompt" in help_text or "--print" in help_text


def _help_text(executable: str) -> str | None:
    """--help 출력을 가져온다. 실패하면 None."""
    path = shutil.which(executable)
    if path is None:
        return None
    result = subprocess.run(
        [path, "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    return (result.stdout or "") + (result.stderr or "")


def inspect_providers() -> list[ProviderStatus]:
    """로컬 claude/kimi 상태를 수집한다. 모델 호출은 하지 않는다."""
    statuses: list[ProviderStatus] = []
    claude_help = _help_text("claude")
    if claude_help is None:
        statuses.append(ProviderStatus("glm", False, False, "claude CLI가 없습니다."))
    elif claude_supports_isolated_print(claude_help):
        statuses.append(
            ProviderStatus(
                "glm",
                True,
                True,
                "claude --bare/--tools/--no-session-persistence 가 보입니다. 키는 자식 환경에만 넣습니다.",
            )
        )
    else:
        statuses.append(ProviderStatus("glm", True, False, "필요한 claude 플래그가 없어 paste만 가능합니다."))
    kimi_help = _help_text("kimi")
    if kimi_help is None:
        statuses.append(ProviderStatus("kimi", False, False, "kimi CLI가 없습니다. paste를 사용하세요."))
    elif kimi_supports_print(kimi_help):
        # -p 는 도구 자동 승인이므로 v1에서는 실행하지 않는다.
        statuses.append(
            ProviderStatus("kimi", True, False, "kimi -p 는 도구를 자동 승인합니다. v1은 paste만 허용합니다.")
        )
    else:
        statuses.append(ProviderStatus("kimi", True, False, "원샷 플래그를 확인하지 못했습니다. paste만 가능합니다."))
    statuses.append(ProviderStatus("paste", True, True, "벤더 프로세스 없이 패킷만 출력합니다."))
    return statuses
