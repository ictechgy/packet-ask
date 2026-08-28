# packet-ask

[English](README.md) | 한국어

스크럽된 패킷만 **서브** 에이전트에 보내는 로컬 CLI입니다. 메인은 지금 세션을 돌리는 에이전트입니다.

이 도구는 고른 파일·diff만 스크럽해서, 공식 도구가 그것만 보게 하거나, 붙여넣을 `packet.md`를 만듭니다.

> 이 도구는 의도적으로 보내는 범위를 줄입니다. 유출이 없음도, 학습되지 않음도 보장하지 않습니다. 벤더 약관은 그대로입니다. 자세한 내용은 [SECURITY.ko.md](SECURITY.ko.md)를 보세요.

MIT 라이선스입니다. 전문은 [LICENSE](LICENSE)를 보세요.

- PyPI: [pypi.org/project/packet-ask](https://pypi.org/project/packet-ask/)
- 저장소: [github.com/ictechgy/packet-ask](https://github.com/ictechgy/packet-ask)

## 필요 조건

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 0.10.9+ (`pyproject.toml` 의 `uv_build` 하한과 같습니다)
- GLM / Claude 서브 실행: allowlist 경로의 `claude` CLI (출처 서명은 검증하지 않습니다)
- Kimi 실행: allowlist 경로의 `kimi` CLI
- `paste` / `grok` / `agy` 는 벤더를 띄우지 않고 패킷만 출력합니다

키는 환경변수로만 넘깁니다. **이 도구는 `.env` 파일을 읽지 않습니다.** 명령줄에 키 값을 적지 마세요. 셸 히스토리에 남습니다. `.env` 는 저장소에서 무시합니다. 변수 이름은 [.env.example](.env.example)을 참고하세요. 실행 파일을 찾는 allowlist 는 [SECURITY.md](SECURITY.md)를 보세요. `doctor`는 help에 플래그가 보이는지만 확인하며, 무도구를 증명하지 않습니다.

## 설치

```bash
uv tool install packet-ask
packet-ask install-skills
packet-ask doctor
```

이미 설치했다면 `uv tool upgrade packet-ask` 입니다. `pipx install packet-ask` 나 가상환경의 `pip install packet-ask` 도 됩니다.

GitHub 기본 브랜치에서 직접:

```bash
uv tool install git+https://github.com/ictechgy/packet-ask
```

로컬 체크아웃:

```bash
uv sync
uv run packet-ask doctor
```

`install-skills` 가 `~/.claude/skills/packet-ask`, `~/.grok/skills/packet-ask`, `~/.codex/skills/packet-ask`, `~/.agents/skills/packet-ask` 에 `SKILL.md` 를 넣습니다. 이후 `/packet-ask` 또는 「kimi로 리뷰」처럼 말하면 메인이 이 CLI를 호출합니다.

## 범위

`review` 는 아래 중 **하나를 명시**해야 합니다. 플래그 없이 워킹 트리 전체를 보내지 않습니다.

| 플래그 | 보내는 것 |
| --- | --- |
| `--files` | 지정한 파일 |
| `--diff` | 지정한 git 범위 |
| `--staged` | 스테이징된 diff |
| `--unstaged` | 워킹 트리 미커밋 diff |

`research` 는 로컬 파일·diff를 기본으로 넣지 않습니다. 예외는 `--include-files` 뿐입니다. `--diff` / `--staged` 는 거절합니다.

## 사용

```bash
packet-ask providers

# 벤더를 실행하지 않고 패킷만 본다
packet-ask review --provider paste --files src/app.py --question "이 코드의 경쟁 상태를 찾아줘"
packet-ask review --provider paste --files src/app.py --json --question "이 코드의 경쟁 상태를 찾아줘"

# GLM. 키는 PACKET_ASK_GLM_KEY
packet-ask review --provider glm --diff HEAD --question "이 변경을 리뷰해줘"

# 워킹 트리 미커밋 diff. 플래그 없이 review 하면 보내지 않습니다
packet-ask review --provider paste --unstaged --question "이 변경을 리뷰해줘"

# Anthropic Claude 서브. 키는 PACKET_ASK_CLAUDE_KEY. 부모 BASE_URL 은 바꾸지 않습니다
packet-ask review --provider claude --files src/app.py --question "이 코드를 리뷰해줘"

# Kimi. 키는 PACKET_ASK_KIMI_KEY
packet-ask review --provider kimi --files src/app.py --question "이 코드를 리뷰해줘"

# grok/agy 는 아직 무도구 원샷을 실행하지 않고 paste 합니다
packet-ask review --provider grok --files src/app.py --question "이 코드를 리뷰해줘"

# 리서치. 로컬 파일·diff 는 기본 금지. 첨부는 --include-files 만
packet-ask research --provider paste --question "Tailwind v4 마이그레이션에서 자주 깨지는 점"

packet-ask doctor
```

Kimi는 공식 `kimi --quiet` 원샷입니다. 대화형 세션을 열지 않습니다. `PACKET_ASK_KIMI_KEY` 가 없으면 실행하지 않습니다. 도구는 `tools: []` 에이전트 파일과 매칭되지 않는 `[tools] enabled` 로 끄고, `KIMI_CODE_HOME` 은 `~/.config/packet-ask/providers/kimi/kimi-code` 격리 프로필만 씁니다. 실제 레포에서 `kimi`를 직접 실행하지 마세요.

GLM은 공식 `claude` 바이너리를 쓰되, **부모 셸의 `ANTHROPIC_BASE_URL` 은 바꾸지 않습니다.** 자식 환경에만 [Z.ai Claude Code 연동](https://docs.z.ai/scenario-example/develop-tools/claude) 엔드포인트와 `PACKET_ASK_GLM_KEY` 를 넣습니다.

패킷 임시 디렉터리는 git 워크트리가 아니라 OS 캐시에 만듭니다. cwd는 샌드박스가 아닙니다. `PACKET_ASK_CACHE_DIR` 을 워크트리 안으로 두면 거절합니다. `.gitignore` 의 `.packet-ask-tmp/` 와 `packet.md` 는 예전 산출물이나 실수로 만든 파일을 커밋하지 않기 위한 방어입니다.

사용자 설정 `~/.config/packet-ask/providers.toml` 은 **paste 별명만** 추가합니다. 실행 파일·argv·env 는 받지 않습니다.

```toml
version = 1
[providers.gemini]
label = "Gemini CLI"
```

## 스킬

`packet-ask install-skills` 가 하니스 홈에 설치합니다. 스킬은 `packet-ask`를 부르라고만 적습니다. 격리는 CLI가 강제합니다.

## 종료 코드

`10`–`14`는 벤더 프로세스를 시작하지 않았다는 뜻입니다.

| 코드 | 의미 |
|---:|---|
| 0 | 성공 |
| 1 | 내부 오류 |
| 2 | 인자 오류 |
| 10 | 정책 거부 (구현·장애 등) |
| 11 | 스코프 거부 |
| 12 | 리댁션/재검증 실패 |
| 13 | 실행 조건을 확인하지 못함 |
| 14 | 용량 초과 |
| 20 | 프로바이더/키 없음 |
| 21 | 프로바이더 실행 실패 |
| 22 | 출력 가드 실패 (전용 키 유출 또는 과대 출력) |

## 개발

```bash
uv sync --group dev
uv run pytest
```

기여는 [CONTRIBUTING.md](CONTRIBUTING.md)를 따릅니다.

## 라이선스

[MIT](LICENSE) Copyright (c) 2026 Coden
