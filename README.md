# packet-ask

개인용 Kimi Code·GLM Coding Plan 구독을 **메인 에이전트가 아니라 서브**로 쓰기 위한 로컬 CLI입니다.

메인(Grok / Claude / Codex)은 워크트리를 그대로 다룹니다. 이 도구는 고른 파일·diff만 스크럽해서, 공식 도구가 그것만 보게 하거나, 붙여넣을 `packet.md`를 만듭니다.

> 이 도구는 의도적으로 보내는 범위를 줄입니다. 유출이 없음도, 학습되지 않음도 보장하지 않습니다.

## 설치

```bash
cd ~/Desktop/packet-ask
uv sync
uv run packet-ask doctor
```

원하면 `uv tool install .` 로 `packet-ask` 명령을 전역에 올릴 수 있습니다.

## 사용

```bash
# 벤더를 실행하지 않고 패킷만 본다
packet-ask review --provider paste --files src/app.py --question "이 코드의 경쟁 상태를 찾아줘"

# GLM 개인 코딩 플랜. 전역 Anthropic 키가 아니라 PACKET_ASK_GLM_KEY
export PACKET_ASK_GLM_KEY="..."
packet-ask review --provider glm --diff HEAD --question "이 변경을 리뷰해줘"

# Kimi Code. TUI를 열지 않고 원샷한다. 전역 kimi 홈이 아니라 PACKET_ASK_KIMI_KEY
export PACKET_ASK_KIMI_KEY="..."
packet-ask review --provider kimi --files src/app.py --question "이 코드를 리뷰해줘"

# 리서치. 로컬 파일은 기본 금지
packet-ask research --provider paste --question "Tailwind v4 마이그레이션에서 자주 깨지는 점"

packet-ask doctor
```

Kimi는 공식 `kimi --quiet` 원샷입니다. 대화형 세션을 열지 않습니다. 도구는 `tools: []` 에이전트 파일로 끄고, `KIMI_CODE_HOME` 은 `~/.config/packet-ask/providers/kimi` 격리 프로필만 씁니다. 실제 레포에서 `kimi`를 직접 실행하지 마세요.

## 스킬

`skills/claude`, `skills/grok`, `skills/codex` 를 각 도구의 스킬 디렉터리에 복사하세요. 스킬은 `packet-ask`를 부르라고만 적습니다. 격리는 CLI가 강제합니다.

## 종료 코드

`10`–`14`는 벤더 프로세스를 시작하지 않았다는 뜻입니다.

| 코드 | 의미 |
|---:|---|
| 0 | 성공 |
| 10 | 정책 거부 (구현·장애 등) |
| 11 | 스코프 거부 |
| 12 | 리댁션/재검증 실패 |
| 13 | 실행 조건을 못 증명 (paste 사용) |
| 14 | 용량 초과 |
| 20 | 프로바이더/키 없음 |
| 21 | 프로바이더 실행 실패 |

## 개발

```bash
uv run pytest
```
