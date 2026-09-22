# Agent Instructions

이 저장소에서 일하는 모든 에이전트가 먼저 읽는 파일이다. 하위 디렉터리의
`AGENTS.md` 는 그 서브트리 안에서 이 파일을 덮어쓴다.

## 이 도구가 무엇인가

로컬 CLI **packet-ask** 는 MAIN(지금 세션)을 실제 워크트리에 두고, SUB 벤더
CLI 에는 **의도적으로 고른 뒤 스크럽한 패킷만** 넘긴다.

유출 없음도 학습 금지도 주장하지 않는다. 이 문장은 마케팅 문구가 아니라
설계 제약이다. 어떤 변경도 이 도구가 실제보다 더 많이 보장하는 것처럼
읽히게 만들면 안 된다.

## 절대 하지 않는 것

아래는 합의된 거절 목록이다. **다시 제안하지 말 것.**

- 커스텀 HTTP 클라이언트. 공식 CLI 원샷만 쓴다.
- 부모 프로세스의 `ANTHROPIC_BASE_URL` 등 환경 변수 변경.
- 사용자 TOML 에서 실행 파일·argv·env 지정. provider overlay 는 paste 별명뿐이고
  내장 id 를 이름으로 쓰는 것 자체가 거절된다.
- 사용자 설정으로 **벤더 동작**을 정하는 것. 모델·argv 기본값은 열지 않는다.
  0.9.0 의 `allowlist.toml` 이 유일한 예외이고 그것도 스코프의 **추정 하나만**
  연다. 새 사용자 설정을 제안하려면 무엇을 열지 않는지부터 적어라.
- 워커 팜, 병렬 팬아웃, 재시도.
- `--all` 이나 암묵적 전체 레포·원격 URL 수집.
- SUB 에게 구현·패치 적용·장애 대응 위임.
- 클립보드 연동, 레포 전체 랭킹, 패킷 자동 분할, tree-sitter 압축.

## 언어와 커밋

- 대화·주석·커밋 본문은 한국어. 식별자는 영어.
- 커밋은 Conventional Commits. 한 커밋에 문서와 동작 변경을 섞지 않는다.
- 브랜치는 `feature/` `fix/` `refactor/` `chore/` `release/`.
- **`main` 에 직접 커밋하지 않는다.** `gh pr merge` 가 로컬 `main` 을 체크아웃
  하면 태그만 달고 바로 작업 브랜치로 떠난다.

## 작업 순서

1. `git fetch origin && git checkout -b <타입>/<주제> origin/main`
2. 동작을 바꾸기 전에 **실패하는 테스트를 먼저** 넣는다.
3. 구현하고 `uv run pytest` 를 통과시킨다.
4. PR 을 열고 독립 리뷰를 받는다.
5. 반영·기각을 커밋 메시지에 근거와 함께 남긴다.

## 리뷰 규약

- **자기 승인 금지.** 동작을 바꿨으면 별도 리뷰 패스를 거친다.
- 이 저장소의 관례는 packet-ask 자신으로 자기 diff 를 리뷰시키는 것이다.

  ```bash
  uv run --offline packet-ask review --provider glm --credential-source keychain \
    --diff origin/main...HEAD --progress --question-stdin < <질문파일>
  ```

- **로컬 변경을 리뷰할 때는 `uv run --offline packet-ask` 를 쓴다.** PATH 의
  `packet-ask` 는 PyPI 설치본이라 방금 고친 코드가 없다. 이것 때문에 고친
  결함이 그대로 재현돼 한참 헤맨 적이 있다.
- 리뷰용 diff·패킷 조각은 `.packet-ask-tmp/` 에 둔다. 루트의
  `.packet-ask-surface` 가 선언한 접두어 밖은 exit 11 로 거절되므로, 저장소
  루트에 흩뿌리면 자기 diff 를 리뷰하지 못한다.
- 질문은 `--question-stdin` 으로 넘긴다. `--question` 은 argv 라 프로세스
  목록과 셸 히스토리에 보인다.
- **질문에 "너에게는 도구가 없다" 를 적는다.** SUB 는 무도구 원샷으로 돌지만
  스스로 그것을 모른다. 이 문단이 없을 때 GLM 이 `ls`·`cat`·`sed` 를 실행한
  것처럼 셸 세션과 파일 내용을 통째로 지어냈고, 넣었더니 근거 없는 항목을
  "패킷 밖" 으로 정확히 표시했다.

  ```
  너에게는 도구가 없다. 파일시스템도 셸도 못 쓴다. 이 패킷이 네가 가진
  전부다. 파일을 열거나 명령을 실행한 것처럼 쓰지 마라. 패킷에 없는 코드를
  인용하지 마라. 모르면 "패킷 밖" 이라고 표시해라.
  ```

## SUB 답변을 믿는 정도

**답변을 그대로 채택하지 않는다.** 리뷰어는 패킷만 본다. 아래는 이 저장소에서
실제로 겪은 것이고 전부 되풀이될 수 있다.

- **"확인 필요" 류 지적은 반드시 로컬에서 재현한다.** 기각한 건은 기각 근거를
  커밋 메시지에 남긴다.
- **SUB 가 패킷 밖이라고 표시한 추정은 특히 의심한다.** 그 위에 세운 blocker 가
  세 번 중 두 번 틀렸다. 다만 밑에 깔린 지적은 옳은 경우가 많았으니 주장과
  근거를 분리해서 본다.
- **패킷에 없는 것을 인용하면 그 응답 전체의 사실 주장을 의심한다.** 존재하지
  않는 함수와 테스트 리터럴을 인용한 리뷰가 있었고, 그 위에 세운 blocker 의
  근거("변경 전에는 매치됐다")는 실측하니 거짓이었다. 조작된 맥락은 그럴듯한
  blocker 를 만들어 낸다.
- **"변경 전에는 X 였다" 류 대조 주장은 양쪽을 다 측정한다.** 한쪽만 보면
  방향을 반대로 읽는다.
- **여러 SUB 의 만장일치는 독립성의 증거가 아니라 공통 입력의 증거일 수 있다.**
  같은 문서 패킷을 받았으면 그 문서의 공백도 함께 물려받는다. 네 출처가 만장
  일치로 상위에 올린 항목이 실측 한 번에 구현 불가로 판명된 적이 있다. 합의가
  특정 항목을 최상위로 밀면, 착수 전에 그 항목이 기대는 **문서 밖 전제**부터
  로컬에서 측정한다.

## 검증

머지 전에 아래가 전부 통과해야 한다. lint 도구는 쓰지 않는다.

```bash
uv run pytest
uv build
release_version=$(uv version --short)
uv run --isolated --no-project --with "dist/packet_ask-${release_version}-py3-none-any.whl" tests/smoke.py
uv run --isolated --no-project --with "dist/packet_ask-${release_version}.tar.gz" tests/smoke.py
```

"통과할 것이다"가 아니라 **실행한 출력**으로 완료를 보고한다. 실패했으면
실패했다고 말한다.

## ExitZero CI 게이트

- CI의 Python 3.11·3.13 테스트 단계는 ExitZero 0.6.1로 기존 pytest를 실행한다.
  로컬에서는 `uv sync --frozen --group dev` 후
  `uv run --offline --frozen python .github/scripts/integrity_base.py`로 기준을 준비하고
  `uvx --from exitzero==0.6.1 exitzero check --format json`을 쓸 수 있다.
  기준 준비가 실패하면 게이트를 실행하지 않는다.
- 정책 명령은 `uv run --offline --frozen pytest`에 JUnit 보고서 저장만 추가한다.
  테스트를 생략하거나 종료 코드를 무시하지 않는다. 기존 build·smoke는 별도 단계다.
- `.exitzero/runs/*.json`과 pytest가 생성한 `.exitzero/pytest.xml`을 각각
  `if: always()`로 업로드해 14일 보존한다. 영수증이 없으면 업로드도 실패한다.
  정책 로딩·명령 시작 실패나 시간초과에는 JUnit이 없을 수 있으며 그 경우 CI에
  경고를 남긴다. 실패 판정은 게이트 종료 코드와 영수증을 기준으로 한다.
  로컬 `.exitzero/`는 커밋하거나 SUB 패킷에 넣지 않는다.
- 아래 생성 구역은 직접 고치지 않는다. `exitzero.toml`을 바꾼 뒤 같은 버전의
  `exitzero init --sync`로 갱신하고 `exitzero lint-config --format json`을 확인한다.
- GitHub `main`은 PR로 반영하며 `test (3.11)`·`test (3.13)`을 필수 검사로 둔다.
  검사 제공자는 GitHub Actions이고 최신 `main` 기준 검사가 필요하다. 관리자도
  같은 규칙을 적용받으며 force push·브랜치 삭제는 허용하지 않는다.
- GitHub 승인 인원은 0명으로 두지만 위 독립 리뷰 규약은 그대로 따른다.
  검사 이름이나 워크플로를 바꿀 때는 GitHub 보호 설정과의 일치도 확인한다.
- 필수 검사 설정은 GitHub 서버에 있으며 이 파일만으로 적용되지 않는다.
  아래 로컬 권한 검사는 아직 GitHub 필수 검사로 연결되지 않았다.
  별도 발급 주체의 서버 권한 검사와 클라이언트 훅은 별도 단계다.

## 테스트 무결성

- `tests/**/*.py`를 Git 기준과 비교해 기존 파일/테스트 함수 삭제, 파일별 단언 수
  감소, 기존 파일에 추가된 skip/xfail을 차단한다. 결과를 재사용하지 않는다.
- CI는 전체 Git 이력을 받고 PR의 base SHA, main push의 before SHA, 작업 브랜치
  push의 `origin/main` 공통 조상을 전용 `refs/exitzero/test-integrity-base`에 고정한다.
  이미 커밋된 삭제도 비교하도록 현재 HEAD를 자동 기준으로 삼지 않는다.
- 로컬 기본 기준도 `origin/main`과의 공통 조상이다. 의도적으로 다른 기준을
  확인할 때는 준비 스크립트의 `--base REF`를 쓴다. 누락되거나 해석할 수 없는
  CI 기준은 기존 pin과 증거를 무효화하고 exit 2로 종료한다.
- `.exitzero/test-integrity-base.json`의 기준/후보 SHA와 선택 방식을 CI에서
  14일 보존한다. 이 파일은 서명된 권한 증명이 아니며 Git ref 자체는 수정 가능하다.
- 정당한 테스트 삭제·이름 변경·skip 추가도 차단될 수 있으므로 별도 리뷰로 판단한다.
  정적 개수·이름·일부 표기 비교이며, 새 파일의 skip이나 같은 개수의 약한 단언,
  동적 별칭과 의미적 검증 품질을 증명하지 않는다. 정책/워크플로 자체 보호는 후속이다.

## 로컬 권한 구역

- `.github/authority.toml`은 기존 테스트 정책과 분리된 권한 정책이다. 검토한 Git
  커밋에 이 파일이 있어야 하며 `--trust-base`로 그 커밋을 명시한다.
- 소스 Python과 일반 문서는 editable, 테스트·의존성·보안/라이선스 문서는
  protected, CI·정책·에이전트 지침·공개 경로 목록은 immutable이다. 겹치면
  더 강한 구역이 우선하고 미분류 경로는 거절한다.
- 프로젝트 밖에 설치한 검증된 ExitZero 0.6.1을 격리 모드로 실행한다:
  `TRUSTED_PYTHON -I -m exitzero --root REPO --policy .github/authority.toml check --trust-base REVIEWED_COMMIT --format json`.
  `TRUSTED_PYTHON`, `REPO`, `REVIEWED_COMMIT`은 실제 경로와 검토 커밋으로 바꾼다.
  후보 저장소의 `uv run`이나 스크립트로 이 검사를 시작하지 않는다.
- 이 정책은 파일 권한 비교와 AST 구문 검사만 한다. 후보 코드·테스트를 실행하지
  않으며 영수증은 후보 밖의 증거 저장소에 즉시 복사한다. 서명된 권한 증명은 아니다.
- protected 변경은 review_required, immutable 변경은 denied로 실패한다.
  로컬 승인 파일이나 모델 설명으로 해제하지 않는다. 정당한 변경은 정확한 커밋·
  tree·변경 경로와 독립 리뷰를 별도 기록한 뒤 운영자가 신뢰 기준을 명시적으로 갱신한다.
- 이 로컬 기능을 CI 자체의 변경 우회를 막는 서버 장벽으로 표현하지 않는다.
  기존 GitHub Actions와 구분되는 검사 발급 주체가 준비되기 전에는 필수 검사로 추가하지 않는다.

## 범위 규율

- 한 배치를 합의 없이 넓히지 않는다. 요청받지 않은 리팩터링을 끼워 넣지 않는다.
- 기존 코드를 고치기 전에 그 코드의 의도와 맥락을 먼저 파악한다.
- 새 파일을 만들기 전에 비슷한 역할의 기존 파일이 있는지 확인한다.
- `HANDOFF.md` 와 `.serena/` 와 `.omc/` 는 `.gitignore` 대상이다. 커밋하지 않는다.
  `HANDOFF.md` 는 다음 에이전트를 위한 로컬 노트이니 배치를 끝내면 갱신한다.

## Scoped Guidance Index

아래 파일들은 해당 디렉터리 아래에서 작업할 때 자동으로 활성화된다. 링크는
찾아보기용이고, 권위는 파일의 위치에서 나온다.

- [src/packet_ask/AGENTS.md](src/packet_ask/AGENTS.md) — 구현 불변식, egress
  표면 구분, 값과 출처 분리, 요청과 기본값, 가드를 여는 규칙, 출력 계약,
  메시지 카탈로그, 종료 코드.
- [tests/AGENTS.md](tests/AGENTS.md) — TDD 순서, 결로 고정과 양성 대조,
  계약 테스트, 수집·캐시 함정.
- [docs/AGENTS.md](docs/AGENTS.md) — 설계 불변식 번호, 과잉 약속 금지,
  근거를 쓰기 전에 측정하기, 영/한 parity 와 그 한계.
- [.github/AGENTS.md](.github/AGENTS.md) — CI 핀, 릴리스,
  Trusted Publishing, 배포 뒤 실측.

<!-- exitzero:begin -->
## exitzero policy

Generated from policy. Edit the TOML, then run `exitzero init --sync`.
Run `exitzero check` before merge; keep the JSON receipt as evidence.
Run `exitzero lint-config` after changing agent configuration.

Required checks:
- `test-integrity`: `python.test-integrity` (tests/**/*.py)
- `regression-suite`: `command` (src/**/*.py, src/packet_ask/data/**, tests/**/*.py, pyproject.toml, uv.lock, .python-version, .gitignore, .packet-ask-surface, .github/**/*.yml, .github/**/*.toml, .github/scripts/*.py, AGENTS.md, CLAUDE.md, CONTRIBUTING.md, README.md, README.ko.md, SECURITY.md, SECURITY.ko.md, docs/**/*.md, skills/**, src/**/AGENTS.md, tests/AGENTS.md, .github/AGENTS.md, LICENSE, env.example)
- Rule `ci-receipts`: CI 게이트의 종료 코드와 저장된 영수증을 확인한다. pytest가 생성한 JUnit 보고서로 테스트 실패를 확인한다.

Policy SHA-256: `aa5e0cd6392c3923a48880c2510eb2535d4517766089f0f52cd2f50d4714dcba`
<!-- exitzero:end -->
