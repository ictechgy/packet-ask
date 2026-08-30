# 보안

[English](SECURITY.md) | 한국어

packet-ask는 보내는 범위를 줄이기 위한 도구입니다. **유출 없음**도 **학습 금지**도 보장하지 않습니다.

개인 Kimi Code·GLM Coding Plan 구독의 데이터 처리는 각 벤더 약관이 정합니다. 이 CLI를 쓴다고 벤더 정책이 바뀌지 않습니다.

## 이 도구가 하는 일

- 워크트리에서 고른 파일·diff만 읽습니다.
- 시크릿·홈 경로·이메일·전화 패턴을 가린 뒤, 다른 패턴으로 다시 검사합니다. 재검증이 실패하면 벤더를 실행하지 않습니다. 패턴 목록은 denylist이며 모든 비밀을 잡지 않습니다.
- 패킷은 원본 레포가 아니라 OS 캐시 전용 디렉터리에 만들고, 정상 종료 시 지웁니다. 강제 종료·크래시 뒤에는 남을 수 있습니다. 삭제는 일반 파일 삭제이며 안전한 소거가 아닙니다.
- allowlist 디렉터리에서 `claude`/`kimi` 실행 파일을 찾습니다. 사용자 소유이고 그룹·기타에 쓸 수 없어야 합니다. **배포 출처·서명은 확인하지 않습니다.**
- 부모 셸의 `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` 는 복사하지 않습니다.
- GLM은 [Z.ai Claude Code 연동](https://docs.z.ai/scenario-example/develop-tools/claude)처럼 자식 환경에만 엔드포인트와 resolve한 전용 GLM credential을 넣습니다.
- resolve한 Kimi credential은 자식 환경으로만 넘기고 `config.toml` 에 쓰지 않습니다.
- credential source는 전용 환경변수, packet-ask 소유 macOS Keychain 항목, 일회성 no-echo prompt로 제한합니다. `auto`는 env 다음 canonical Keychain만 보고 자동 prompt하지 않습니다.
- Keychain은 shell 없이 고정 `/usr/bin/security` argv와 최소 환경으로 접근합니다. status는 password를 읽지 않고 존재만 보며, `credentials set`은 `security -w`가 직접 물어 key를 argv·shell history에 넣지 않습니다.
- Keychain `--access command`는 background agent 사용을 위해 `/usr/bin/security`를 신뢰하며 key의 at-rest 보호만 제공합니다. 같은 사용자 권한의 다른 프로세스에 대한 경계는 아닙니다. `--access prompt`는 어떤 앱도 신뢰하지 않아 headless session에서 쓸 수 없을 수 있습니다.
- `doctor`는 `--help`에 필요 플래그 이름이 있는지 확인합니다. 실제 무도구·OS 샌드박스를 증명하지 않습니다. 런치는 고른 바이너리만 프로브합니다. `doctor`는 카탈로그 전체를 돌되 `--help` 를 경로·mtime·크기로 프로세스 동안 캐시합니다.
- GLM과 Claude 자식 환경에는 `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_ERROR_REPORTING=1` 을 넣습니다. 벤더가 플래그를 무시할 수 있습니다. 이 CLI는 `~/.claude/projects/` 를 지우지 않습니다.
- 벤더 stdout에 전용 키 값이 있거나 출력이 너무 크면 폐기하고 종료 코드 22를 반환합니다.
- 벤더 stdin·stdout·stderr는 하나의 bounded nonblocking deadline을 공유합니다. 명시 파일, stdin 질문, git diff 수집도 설정한 한도에서 읽기를 멈춥니다.
- 원본 조각뿐 아니라 최종 렌더링한 `packet.md`가 `--max-bytes` 안에 들어야 합니다. 바이너리와 비 UTF-8 명시 파일은 거절합니다.
- 벤더 출력의 ANSI CSI/OSC/DCS 및 안전하지 않은 제어문자를 제거하고, 영수증 경로는 JSON 이스케이프합니다.
- 도구 소유 프로바이더 프로필 디렉터리는 최종 경로 심링크를 거절합니다. Kimi 세션 정리 실패는 숨기지 않고 보고합니다.
- worktree discovery, diff 수집, packet-local Git 초기화는 하나의 bounded runner를 쓰며 timeout·출력 초과·interrupt에서 process group을 종료합니다.
- 임시 packet을 제거한 뒤에만 성공 출력을 내보냅니다. cleanup 실패는 기존 provider 실패 코드를 바꾸지 않습니다.
- receipt와 manifest의 redaction metadata는 음이 아닌 정수 count allowlist만 직렬화하며 내부 report 필드는 포함하지 않습니다.

## 이 도구가 하지 않는 일

- 벤더가 패킷을 학습·보관하지 못하게 막지 않습니다.
- cwd를 샌드박스로 취급하지 않습니다. 서브 CLI의 cwd는 패킷 디렉터리입니다.
- 구현·패치 적용·운영 장애 대응을 서브로 보내지 않습니다.
- 사용자 zsh 함수/`PATH` 앞쪽 래퍼를 실행하지 않습니다. 기본 allowlist는 `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`, `/bin`, `~/.local/bin` 과 `PACKET_ASK_*_BIN` 입니다.
- Claude 오토메모리가 꺼졌음을 증명하지 않습니다. 자식 env 플래그는 벤더가 존중할 수도 있는 스위치일 뿐입니다.
- 구현·장애 문구 게이트는 어휘 기반 최선 노력 검사이며 의미적 의도를 증명하지 않습니다.
- ZCode, Claude Code, `.env`, 임의 key 파일, password-manager 저장소, 사용자 key command를 탐색하지 않습니다. 외부 관리자는 전용 환경변수를 주입해야 합니다.

## 키와 프로필

| 변수 | 용도 |
| --- | --- |
| `PACKET_ASK_GLM_KEY` | GLM Coding Plan 키. 전역 Anthropic 키 금지 |
| `PACKET_ASK_CLAUDE_KEY` | Anthropic Claude 서브 키. 전역 Anthropic 키 금지 |
| `PACKET_ASK_KIMI_KEY` | Kimi 키. 디스크에 쓰지 않음 |
| `PACKET_ASK_CACHE_DIR` | 패킷 캐시 부모. 절대경로만. 전용 `packet-ask` 자식을 만듭니다 |
| `PACKET_ASK_CLAUDE_BIN` / `PACKET_ASK_KIMI_BIN` | 절대경로 실행 파일 재지정 |
| `PACKET_ASK_BIN_DIRS` | allowlist 디렉터리 추가 (`os.pathsep` 구분, 절대경로만) |

canonical macOS Keychain service는 `packet-ask-glm`, `packet-ask-kimi`,
`packet-ask-claude`이며 현재 uid의 계정 이름을 씁니다. Keychain·prompt를
포함해 실제로 고른 모든 키는 터미널 제어문자 제거 전후의 프로바이더 출력과
대조합니다. 자세한 계약은 [docs/key-sources.md](docs/key-sources.md)에 있습니다.

키를 저장소·이슈·패킷에 넣지 마세요. `.env` 와 개인키 파일명은 수집 단계에서 거절합니다.

격리 프로필 `~/.config/packet-ask/providers/<id>` 는 실행 후에도 남을 수 있습니다.

## 취약점 보고

공개 이슈에 시크릿을 붙이지 마세요. GitHub Security Advisories로 비공개 보고하세요: https://github.com/ictechgy/packet-ask/security/advisories

이메일은 `ictechgy@gmail.com` 입니다.
