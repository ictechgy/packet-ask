# 보안

packet-ask는 보내는 범위를 줄이기 위한 도구입니다. **유출 없음**도 **학습 금지**도 보장하지 않습니다.

개인 Kimi Code·GLM Coding Plan 구독의 데이터 처리는 각 벤더 약관이 정합니다. 이 CLI를 쓴다고 벤더 정책이 바뀌지 않습니다.

## 이 도구가 하는 일

- 워크트리에서 고른 파일·diff만 읽습니다.
- 시크릿·홈 경로·이메일·전화 패턴을 가린 뒤, 다른 패턴으로 다시 검사합니다. 재검증이 실패하면 벤더를 실행하지 않습니다.
- 패킷은 원본 레포가 아니라 OS 캐시(`~/Library/Caches/packet-ask` 또는 `~/.cache/packet-ask`)에 만들고, 끝나면 지웁니다.
- 공식 `claude`/`kimi`만 신뢰 경로에서 찾고, 최소 환경·무도구 원샷으로 한 번 실행합니다.
- 부모 셸의 `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` 는 복사하지 않습니다.
- GLM은 [Z.ai 공식 Claude Code 연동](https://docs.z.ai/scenario-example/develop-tools/claude)처럼 자식 환경에만 `ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic` 과 `PACKET_ASK_GLM_KEY` 를 넣습니다.
- Kimi API 키는 `PACKET_ASK_KIMI_KEY` 환경변수로만 넘기고 `config.toml` 에 쓰지 않습니다.

## 이 도구가 하지 않는 일

- 벤더가 패킷을 학습·보관하지 못하게 막지 않습니다.
- cwd를 샌드박스로 취급하지 않습니다. 공식 CLI의 cwd는 패킷 디렉터리입니다.
- 구현·패치 적용·운영 장애 대응을 서브로 보내지 않습니다.
- 사용자 zsh 함수/`PATH` 앞쪽 래퍼를 실행하지 않습니다. `claude` 는 `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`, `/bin`, `~/.local/bin` 과 `PACKET_ASK_CLAUDE_BIN` 만 봅니다.

## 키와 프로필

| 변수 | 용도 |
| --- | --- |
| `PACKET_ASK_GLM_KEY` | GLM Coding Plan 키. 전역 Anthropic 키 금지 |
| `PACKET_ASK_CLAUDE_KEY` | Anthropic Claude 서브 키. 전역 Anthropic 키 금지 |
| `PACKET_ASK_KIMI_KEY` | Kimi 키. 디스크에 쓰지 않음 |
| `PACKET_ASK_CACHE_DIR` | 패킷 캐시 위치 재지정 |
| `PACKET_ASK_CLAUDE_BIN` / `PACKET_ASK_KIMI_BIN` | 절대경로 실행 파일 재지정 |
| `PACKET_ASK_BIN_DIRS` | 신뢰 디렉터리 추가 (`os.pathsep` 구분) |

키를 저장소·이슈·패킷에 넣지 마세요. `.env` 와 개인키 파일명은 수집 단계에서 거절합니다.

## 취약점 보고

공개 이슈에 시크릿을 붙이지 마세요. 유지 관리자에게 비공개로 연락하세요.
