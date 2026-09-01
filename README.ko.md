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

credential은 전용 환경변수, packet-ask 소유 macOS Keychain 항목, 명시한 일회성 prompt 중 하나에서 받습니다. **`.env`, ZCode, Claude Code, 임의 key 파일이나 key command는 읽지 않습니다.** 키 값을 명령행에 직접 넣으면 셸 히스토리에 남으므로 금지합니다. 변수 이름만 [.env.example](.env.example)에 있고 전체 source 계약은 [docs/key-sources.md](docs/key-sources.md)에 있습니다. 실행 파일 allowlist는 [SECURITY.md](SECURITY.md)를 보세요. `doctor`는 제한된 help 출력에 필요한 플래그 이름이 있는지만 보며 무도구 샌드박스를 증명하지 않습니다.

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

`inspect review`와 `inspect research`는 같은 scrubbed packet을 만들고 검증한
뒤 cleanup이 성공하면 metadata만 출력합니다. provider를 읽거나 credential을
확인하거나 provider timeout을 계산하거나 vendor를 실행하지 않습니다. human
출력은 이스케이프한 한 줄이고 `--json`은 고정 packet summary 필드만 반환합니다.
`--breakdown`은 scrubbed question byte, framing byte, 파일/diff별 scrubbed byte,
논리 줄 수와 allowlisted redaction count를 additive로 보여 줍니다. 줄 수는 scrub
이후 payload에서 LF만 구분자로 사용하며 항목별로만 셉니다(0-byte 파일은 `0`).
diff는 변경 줄만이 아니라 header와 hunk를 포함한 전체 렌더링 diff를 셉니다.
이 항목별 shape metadata는 의도적으로 공개하지만 질문·항목 본문은 반환하지
않습니다.

`--line-numbers`를 명시하면 `packet.md` 안의 scrubbed 전체 파일 본문에 고정 폭
gutter를 붙여 리뷰가 packet-local 줄을 인용할 수 있습니다. `files/` 아래 scrubbed
파일은 바꾸지 않고 unified diff에는 적용하지 않으며 gutter/설명문 byte도 모두
`--max-bytes`에 포함합니다. 번호는 receipt의 packet digest에서만 고정됩니다.
플래그를 끄면 기존 packet byte가 그대로 유지됩니다.

`--selected-tree`를 명시하면 `--files` 또는 `--include-files`로 이미 선택한 경로만
deterministic tree로 추가합니다. 저장소를 다시 탐색하지 않고, 반복 선택은 한 번만
표시하며, ASCII-escaped label을 inert fenced text block 안에 렌더링하고 tree byte도
`--max-bytes`에 포함합니다. diff-only와 question-only packet은 범위를 조용히
넓히거나 flag를 무시하지 않고 거절합니다.

파일 section heading은 일반 Unicode 경로는 유지하지만 줄/제어/bidi, backtick,
HTML delimiter 문자를 escape합니다. `files/` 아래 private artifact는 선택한 실제
파일명을 그대로 유지합니다.

`--max-files`는 명시 파일과 diff 경로 모두에 적용됩니다. `--max-bytes`는
프레이밍과 경로 라벨을 포함한 최종 UTF-8 `packet.md`에 적용됩니다. 입력은
설정한 한도에서 읽기를 멈추며, 명시한 바이너리 또는 비 UTF-8 파일은 거절합니다.

`--preflight-timeout` 기본값은 30초입니다. 실제 fd 질문 stdin, worktree 탐색,
diff name-status, diff 본문, packet-local `git init`이 하나의 monotonic absolute
deadline을 공유하며 각 Git 호출의 기존 30초 상한도 유지합니다. 명시한 양수는
그대로 사용합니다. 일반 파일 read와 CPU redaction은 size로 제한하지만 이
timer가 강제 중단하지는 않습니다.

일반 redaction 뒤에는 detection-only Unicode shadow도 검사합니다. NFKC
compatibility 문자, format control, 동등한 dot·dash, Unicode decimal digit으로
난독화한 international email·phone 후보를 찾습니다. shadow로 packet 원문을
normalize하거나 다시 쓰지 않고 후보가 있으면 fail-closed합니다. known token
family, secret literal, URL userinfo, PEM header도 같은 shadow에서 확인하며
canonical dotted 국내 mobile 번호는 scrub하고 혼합 separator는 fail-closed합니다. 코드 오탐을
줄이기 위해 알려지지 않은 ASCII attribute형 suffix를 가진 모호한 Unicode
행렬곱 표현식은 허용하므로, 여전히 no-leak 보장이 아닌 denylist입니다.

`--timeout`을 생략하면 최종 packet 크기로 launch deadline을 고릅니다.
64 KiB 이하는 1200초, 128 KiB 이하는 1500초, 그보다 크면 1800초입니다.
명시한 `--timeout`은 clamp 없이 그대로 씁니다. 넉넉한 기본값은 성공 호출을
늦추지 않고 실제 hang의 실패 판정만 늦춥니다. Ctrl+C로 중단할 수 있으며 CI와
무인 실행은 timeout을 명시하는 편이 좋습니다. paste/dry-run receipt에도
계산값을 참고용으로 표시하지만 provider deadline은 적용되지 않습니다.

## 사용

```bash
packet-ask providers
packet-ask credentials status

# argv/history에 key를 넣지 않고 macOS Keychain prompt로 저장
packet-ask credentials set glm --store macos-keychain --access command

# 벤더를 실행하지 않고 패킷만 본다
packet-ask review --provider paste --files src/app.py --question "이 코드의 경쟁 상태를 찾아줘"
packet-ask review --provider paste --files src/app.py --json --question "이 코드의 경쟁 상태를 찾아줘"

# packet 본문·provider·credential 접근 없이 metadata만 확인
packet-ask inspect review --files src/app.py --question "경쟁 상태를 찾아줘"
packet-ask inspect review --diff HEAD --json --question "이 변경을 리뷰해줘"
packet-ask inspect review --diff HEAD --breakdown --json --question "packet 크기를 봐줘"

# 로컬 stdin/Git preflight budget만 확대. provider timeout과는 별개
packet-ask inspect review --diff HEAD --preflight-timeout 60 --question "이 변경을 리뷰해줘"

# GLM. auto는 전용 env를 먼저 보고 macOS Keychain packet-ask-glm을 봅니다
packet-ask review --provider glm --credential-source auto --diff HEAD --question "이 변경을 리뷰해줘"

# provider launch 중 30초마다 비민감 heartbeat를 명시적으로 출력
packet-ask review --provider glm --diff HEAD --progress --question "이 변경을 리뷰해줘"

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

Kimi는 공식 `kimi --quiet` 원샷입니다. 대화형 세션을 열지 않습니다. 전용 Kimi credential을 resolve하지 못하면 실행하지 않습니다. 도구는 `tools: []` 에이전트 파일과 매칭되지 않는 `[tools] enabled` 로 끄고, `KIMI_CODE_HOME` 은 `~/.config/packet-ask/providers/kimi/kimi-code` 격리 프로필만 씁니다. 실제 레포에서 `kimi`를 직접 실행하지 마세요.

Kimi session cleanup이 성공하기 전에는 성공 output을 반환하지 않습니다.
provider·output-guard·signal 실패가 이미 있으면 동시에 발생한 session cleanup
실패는 고정 비민감 warning으로만 보고하고 원래 예외·exit status를 보존합니다.

격리 `KIMI_CODE_HOME`을 공유하는 Kimi 실행은 private non-inheritable advisory
lock으로 config 생성부터 provider 실행·session cleanup까지 직렬화합니다. 두 번째
실행이 30초 안에 lock을 얻지 못하면 shared profile이나 vendor를 건드리기 전에
실패합니다.

GLM은 공식 `claude` 바이너리를 쓰되, **부모 셸의 `ANTHROPIC_BASE_URL` 은 바꾸지 않습니다.** 자식 환경에만 [Z.ai Claude Code 연동](https://docs.z.ai/scenario-example/develop-tools/claude) 엔드포인트와 resolve한 전용 GLM credential을 넣습니다. GLM과 Claude 자식 환경에는 `CLAUDE_CODE_DISABLE_AUTO_MEMORY`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_ERROR_REPORTING` 도 넣습니다. 로컬 세션 잔여를 줄이기 위한 것이며, 벤더가 아무것도 저장하지 않음을 증명하지 않습니다.

task 명령의 기본값은 `--credential-source auto`입니다. 전용 환경변수가
우선이고, 그다음 canonical macOS Keychain 항목을 봅니다. `env`, `keychain`,
`prompt`를 명시하면 그 source만 쓰고 fallback하지 않습니다. `prompt`는
대화형 터미널에서만 동작하고 값을 저장하지 않습니다. status는 Keychain
password를 읽지 않고 항목 존재만 확인합니다. 어느 source에서 고른 키든
원문·터미널 정규화 출력의 반사 검사를 거칩니다. task-time read는 고정된
비민감 문구로 missing, inaccessible, timeout, invalid와 indeterminate local
read failure를 구분하고 JSON 오류와 code-level 분류는 계속 generic입니다.
source 선택은 immutable builtin backend registry를 사용하며 `auto`에는
`env` 다음 `keychain`만 들어갑니다. 사용자 backend registration, 임의 command,
key file, 타사 설정 adapter는 없습니다.

성공하면 stderr에 런치 전 영수증(`packet-ask receipt …`)과 완료 후 밀리초 구간(`packet-ask timing …`)을 씁니다. `--json` 에는 `timing` 객체가 들어갑니다. 두 줄 모두 키 값을 넣지 않습니다. 영수증 경로는 JSON 이스케이프하며, 불신뢰 프로바이더 출력의 터미널 제어 시퀀스는 출력 전에 제거합니다.
명시한 `--progress`는 provider 호출 동안 30초마다 stderr에
`packet-ask progress phase=launch elapsed_ms=…`만 추가합니다. 기본은 off이며
provider/path/key/body를 넣지 않고 최종 timing·성공 output 전에 멈춥니다.

`--json`을 쓰면 argparse·runtime 실패도 stdout에 `ok: false`인
`packet-ask.v1` 객체 하나를 반환합니다. `error.code`, `error.kind`, 일반화된
영문 `error.message`만 공개하며 raw argv·예외 원문·경로·키·traceback은 넣지
않습니다. process exit code는 유지합니다. `--json`이 없으면 기존 사람용
stderr 동작을 그대로 유지합니다.

receipt JSON에는 `timeout_seconds`, `timeout_source`(`auto`/`explicit`),
`timeout_applies`가 추가됩니다. `packet-ask.v1`은 additive schema이므로 소비자는
모르는 receipt key를 무시해야 합니다. 밀리초 `timing`의 기존 4개 key는 불변입니다.

패킷 임시 디렉터리는 git 워크트리가 아니라 OS 캐시에 만듭니다. cwd는 샌드박스가 아닙니다. `PACKET_ASK_CACHE_DIR` 을 워크트리 안으로 두면 거절합니다. 새 패킷은 private lease를 잡고, 이후 실행은 lease가 풀린 지 24시간 이상 된 패킷 데이터만 정리합니다. active·fresh·symlink·비공개 권한 위반·lease가 없는 예전 디렉터리는 자동 삭제하지 않습니다. `.gitignore` 의 `.packet-ask-tmp/` 와 `packet.md` 는 예전 산출물이나 실수로 만든 파일을 커밋하지 않기 위한 방어입니다.

모든 Git subprocess는 같은 bounded process-group runner를 씁니다. worktree
discovery, diff 수집, packet-local `git init`이 deadline과 byte limit을
공유합니다. Ctrl+C·SIGTERM·SIGHUP은 등록된 Git/provider 그룹을 종료하고
packet cleanup 뒤 전파됩니다. packet cleanup이
성공한 뒤에만 성공 stdout을 냅니다. packet payload byte/digest와 변경되지
않은 사용자 provider overlay는 프로세스 안에서 재사용해 반복 read/hash/TOML
parse를 피합니다.

사용자 설정 `~/.config/packet-ask/providers.toml` 은 **paste 별명만** 추가합니다. 실행 파일·argv·env·adapter ID·launcher·probe·registration hook은 받지 않습니다. builtin launch dispatch와 doctor probe 종류는 immutable code registry에서만 정합니다.
alias label과 notes는 길이를 제한하고 human/JSON 출력 전에 terminal·bidi·line/
paragraph control을 거절합니다. 정상 언어와 emoji sequence의 ZWNJ·ZWJ는
허용합니다.

구현·장애 질문 게이트는 보수적인 어휘 검사이며 의도를 증명하지는 않습니다.
런치 어댑터는 벤더 도구를 끄고 패킷을 자식 cwd로 사용하지만, 이는 OS 수준
파일시스템 격리가 아닙니다.

```toml
version = 1
[providers.gemini]
label = "Gemini CLI"
```

## 스킬

`packet-ask install-skills` 가 하니스 홈에 설치합니다. 스킬은 `packet-ask`를
부르라고만 적습니다. CLI가 패킷 선택과 벤더 도구 차단을 강제하지만 OS
샌드박스는 아닙니다.

## 종료 코드

`10`–`14`는 벤더 프로세스를 시작하지 않았다는 뜻입니다. task 종료 signal은
관례적인 상태를 유지해 SIGHUP은 129, SIGINT는 130, SIGTERM은 143입니다.

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

현재 confinement 보강의 근거는 [docs/hardening.md](docs/hardening.md)에 있습니다.
runtime/process와 hot-path 결정은 [docs/runtime-hardening.md](docs/runtime-hardening.md)에 있습니다.

기여는 [CONTRIBUTING.md](CONTRIBUTING.md)를 따릅니다.

## 라이선스

[MIT](LICENSE) Copyright (c) 2026 Coden
