# 보안

[English](SECURITY.md) | 한국어

packet-ask는 보내는 범위를 줄이기 위한 도구입니다. **유출 없음**도 **학습 금지**도 보장하지 않습니다.

개인 Kimi Code·GLM Coding Plan 구독의 데이터 처리는 각 벤더 약관이 정합니다. 이 CLI를 쓴다고 벤더 정책이 바뀌지 않습니다.

## 이 도구가 하는 일

- 워크트리에서 고른 파일·diff만 읽습니다.
- `inspect review|research`는 같은 scope·redaction·packet 검증·cleanup을 실행하되 provider나 credential을 읽지 않고 고정 공개 metadata만 출력합니다.
- 명시한 inspect `--breakdown`은 scrubbed byte count, framing byte, 상대경로, 항목별 allowlisted redaction count만 추가합니다. 질문·항목 본문은 반환하지 않습니다.
- 시크릿·홈 경로·이메일·전화 패턴을 가린 뒤, 다른 패턴으로 다시 검사합니다. 재검증이 실패하면 벤더를 실행하지 않습니다. 패턴 목록은 denylist이며 모든 비밀을 잡지 않습니다.
- 재검증은 NFKC compatibility 형식, format control, 동등 dot/dash, Unicode decimal digit, international mailbox label과 phone 후보를 detection-only Unicode shadow에서 봅니다. shadow로 packet을 다시 쓰지 않으며 의심 값은 fail-closed합니다. 소스 코드 오탐을 줄이기 위해 알려지지 않은 ASCII attribute형 suffix의 모호한 Unicode 행렬곱 문법은 허용합니다.
- known token family는 primary scrub과 shadow verify에서 대칭으로 확인합니다. secret literal·URL userinfo·PEM header도 shadow를 사용합니다. canonical dotted 국내 mobile 번호는 scrub하고 dot/dash/space 혼합형은 fail-closed합니다. 일반 E.164 coverage를 주장하지 않습니다.
- 패킷은 원본 레포가 아니라 OS 캐시 전용 디렉터리에 만들고, 정상 종료 시 지웁니다. 새 패킷은 private directory advisory lock과 0600 lease marker를 만듭니다. 이후 실행은 현재 사용자 소유 0700 패킷 디렉터리의 lock을 얻을 수 있고 marker가 24시간 이상 됐을 때만 데이터를 지우며 active·fresh·symlink·비공개 권한 위반·marker가 없는 예전 디렉터리는 건너뜁니다. 따라서 강제 종료 뒤 데이터가 최소 24시간, 그리고 이후 정리 실행 전까지 남을 수 있습니다. 삭제는 일반 파일 삭제이며 안전한 소거가 아닙니다.
- packet cache mkdir/stat/chmod 실패는 경로나 traceback을 노출하지 않는 고정 confinement 오류로 변환합니다.
- allowlist 디렉터리에서 `claude`/`kimi` 실행 파일을 찾습니다. 사용자 소유이고 그룹·기타에 쓸 수 없어야 합니다. **배포 출처·서명은 확인하지 않습니다.**
- 부모 셸의 `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` 는 복사하지 않습니다.
- GLM은 [Z.ai Claude Code 연동](https://docs.z.ai/scenario-example/develop-tools/claude)처럼 자식 환경에만 엔드포인트와 resolve한 전용 GLM credential을 넣습니다.
- resolve한 Kimi credential은 자식 환경으로만 넘기고 `config.toml` 에 쓰지 않습니다.
- credential source는 전용 환경변수, packet-ask 소유 macOS Keychain 항목, 일회성 no-echo prompt로 제한합니다. `auto`는 env 다음 canonical Keychain만 보고 자동 prompt하지 않습니다.
- credential resolve는 immutable builtin backend registry만 사용합니다. `auto`는 env 다음 Keychain으로 고정되며 사용자는 backend·key command·key file·executable·타사 설정 adapter를 등록할 수 없습니다.
- Keychain은 shell 없이 고정 `/usr/bin/security` argv와 최소 환경으로 접근합니다. status는 password를 읽지 않고 존재만 보며, `credentials set`은 `security -w`가 직접 물어 key를 argv·shell history에 넣지 않습니다.
- Keychain `--access command`는 background agent 사용을 위해 `/usr/bin/security`를 신뢰하며 key의 at-rest 보호만 제공합니다. 같은 사용자 권한의 다른 프로세스에 대한 경계는 아닙니다. `--access prompt`는 어떤 앱도 신뢰하지 않아 headless session에서 쓸 수 없을 수 있습니다.
- `doctor`는 `--help`에 필요 플래그 이름이 있는지 확인합니다. 실제 무도구·OS 샌드박스를 증명하지 않습니다. help 프로브에는 하나의 deadline, 합산 출력 상한, 프로세스 그룹 종료를 적용합니다. 런치는 고른 바이너리만 프로브합니다. `doctor`는 카탈로그 전체를 돌되 성공한 `--help`를 경로·mtime·크기로 프로세스 동안 캐시합니다.
- builtin launcher와 doctor probe 종류는 immutable code registry에서만 고릅니다. 사용자 alias에는 adapter ID가 없으며 executable·argv·env·launcher·probe·hook을 등록하거나 선택할 수 없습니다.
- 사용자 alias label/notes는 길이를 제한하고 출력 전에 terminal·bidi·line·paragraph control을 거절합니다. 정상 언어·emoji shaping의 ZWNJ/ZWJ는 허용합니다.
- GLM과 Claude 자식 환경에는 `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_ERROR_REPORTING=1` 을 넣습니다. 벤더가 플래그를 무시할 수 있습니다. 이 CLI는 `~/.claude/projects/` 를 지우지 않습니다.
- 벤더 stdout에 전용 키 값이 있거나 출력이 너무 크면 폐기하고 종료 코드 22를 반환합니다.
- 벤더 stdin·stdout·stderr는 하나의 bounded nonblocking deadline을 공유합니다. 명시 파일, stdin 질문, git diff 수집도 설정한 한도에서 읽기를 멈춥니다.
- 실제 fd 질문 stdin과 모든 Git preflight 호출은 설정 가능한 monotonic deadline 하나를 공유합니다(기본 30초). 각 Git 호출의 개별 30초 상한도 유지합니다. 일반 파일 read와 CPU redaction은 size-bounded지만 이 deadline이 강제 중단하지는 않습니다.
- 원본 조각뿐 아니라 최종 렌더링한 `packet.md`가 `--max-bytes` 안에 들어야 합니다. 바이너리와 비 UTF-8 명시 파일은 거절합니다.
- 벤더 출력의 ANSI CSI/OSC/DCS 및 안전하지 않은 제어문자를 제거하고, 영수증 경로는 JSON 이스케이프합니다.
- 도구 소유 프로바이더 프로필 디렉터리는 최종 경로 심링크를 거절합니다. Kimi 세션 정리 실패는 숨기지 않고 보고합니다.
- Kimi 성공 output은 session cleanup 성공 전까지 보류합니다. provider·output-guard·signal 실패가 이미 있으면 동시에 발생한 Kimi cleanup 실패는 고정 비민감 warning만 내고 primary failure를 바꾸지 않습니다.
- Kimi config·실행·session cleanup은 0600 non-inheritable advisory run lock을 공유합니다. lock 획득은 30초로 제한하며 경쟁 실행은 `KIMI_CODE_HOME` 변경이나 Kimi launch 전에 실패합니다.
- worktree discovery, diff 수집, packet-local Git 초기화는 하나의 bounded runner를 쓰며 timeout·출력 초과·interrupt에서 process group을 종료합니다. task 범위 SIGTERM/SIGHUP handler는 생성한 process group 또는 packet이 등록될 때까지 signal 전달을 미룬 뒤 같은 child·packet cleanup 경로를 재사용합니다.
- 임시 packet을 제거한 뒤에만 성공 출력을 내보냅니다. cleanup 실패는 기존 provider 실패 코드를 바꾸지 않습니다.
- 선택 `--progress`는 고정 launch phase와 음이 아닌 경과 ms만 출력합니다. 기본은 off이며 실제 stderr fd에서는 nonblocking write를 쓰고 최종 timing/output 전에 멈춥니다.
- receipt와 manifest의 redaction metadata는 음이 아닌 정수 count allowlist만 직렬화하며 내부 report 필드는 포함하지 않습니다.
- JSON 실패는 고정 code/kind/message mapping만 사용하며 raw argv·예외 원문·경로·credential·provider stderr·traceback을 직렬화하지 않습니다.

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
