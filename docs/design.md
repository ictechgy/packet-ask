# packet-ask 설계

메인은 지금 세션을 돌리는 에이전트다. 서브만 스크럽된 패킷을 받는다.

실행형 내장: `paste`(출력만), `glm`, `kimi`, `claude`.
paste 전용 내장: `grok`, `agy`. 둘 다 계약을 확인했고 둘 다 격리 런치가
성립하지 않는다. `grok` 은 `--prompt-file`·`--tools`·`--sandbox` 를 갖췄지만 PATH 의
실행 파일이 실제 바이너리를 벤더 홈에서 찾고 전용 키를 받을 env 통로가 없어, 자식
HOME 을 격리 프로바이더 홈으로 바꾸는 이 도구의 런치 규약과 양립하지 않는다.
`agy` 는 태스크를 argv 로 넘겨야 해서 프로세스 목록에 보인다. 플래그 계약만 보고
승격하지 말 것.
사용자 `providers.toml` 은 paste 별명만 추가한다. 실행 파일은 지정할 수 없다.

## 목표

의도적으로 고른 패킷만 공식 도구에 넘긴다. 학습 금지·유출 없음을 보장하지 않는다.

> 이 도구는 의도적으로 보내는 범위를 줄입니다. 유출이 없음도, 학습되지 않음도 보장하지 않습니다.

## 실행 경계

1. 워크트리에서 스코프를 모은다.
2. 시크릿 리댁션 → 신원 스크럽 → 독립 패턴 재검증. 실패하면 벤더를 실행하지 않는다.
3. 0700 임시 패킷을 원본 레포가 아니라 OS 캐시에 만든다. cwd는 샌드박스가 아니다.
4. `doctor`는 지원 버전에서 필요하다고 본 help 플래그가 있는지 확인한다. 무도구를 수학적으로 증명하지 않는다. 불일치하면 실행하지 않는다.
5. Kimi는 TUI를 열지 않고 `kimi --quiet --agent-file`(tools: []) `--work-dir` 패킷으로 원샷한다.
6. 플래그를 확인하지 못하면 paste만 한다.
7. 사용자 개시, 한 건, 재시도 없음, 병렬 없음. task mode 는 `review` 와 `research` 둘뿐이고 문서화된 표면과 CLI 표면이 같아야 한다. review 는 --files/--diff/--staged/--unstaged 중 하나를 요구한다. research 는 로컬 diff 를 받지 않는다.
8. 출력은 untrusted 봉투로 반환하고 패킷을 삭제한다.
9. 런치는 고른 프로바이더만 `--help` 한다. help stdout/stderr는 합산 상한과 하나의 deadline 아래 읽고 실패 시 프로세스 그룹을 끝낸다. `doctor`는 카탈로그 전체를 보되 성공한 같은 바이너리는 canonical 경로·device·inode·mtime·크기로 캐시한다.
10. GLM/Claude 자식은 공식 bare mode, 빈 built-in tools, strict inline empty MCP config를 쓰고 claude.ai MCP·오토메모리·부가 트래픽·에러 리포팅을 끈다. 벤더가 값을 무시할 수 있으며 OS sandbox는 아니다.
11. 성공 시 stderr에 영수증과 밀리초 구간을 쓰고, `--json` 에 `timing` 을 넣는다. 비밀 값은 넣지 않는다.
12. 명시 파일·질문 stdin·git 출력은 예산까지만 읽는다. 최종 `packet.md` 전체가 `--max-bytes` 안에 있어야 한다.
13. 벤더 stdin/stdout/stderr를 하나의 deadline 아래 동시에 처리한다. stdout 선출력으로 stdin 쓰기와 timeout을 막지 못한다.
14. 벤더 출력의 터미널 제어문자를 제거한 뒤 전용 키를 다시 검사한다. receipt 경로는 JSON 이스케이프한다.
15. 프로바이더 프로필의 심링크와 Kimi 세션 정리 실패를 숨기지 않는다.
16. credential source는 `auto`/`env`/`keychain`/`prompt`만 허용한다. `auto`는 전용 env 다음 packet-ask canonical macOS Keychain만 보고 prompt하지 않는다.
17. 실제 선택된 key는 저장 위치와 무관하게 벤더 출력의 원문·터미널 정규화 결과 모두에서 반사 여부를 검사한다.
18. macOS Keychain 저장은 `command`(고정 `/usr/bin/security` 신뢰, background 사용)와 `prompt`(trusted app 없음, 매회 사용자 승인)를 구분한다. 둘의 위협 모델을 같은 것으로 주장하지 않는다.
19. worktree discovery, diff, packet-local `git init`은 같은 bounded Git runner를 써 deadline·출력 상한·interrupt group kill을 공유한다.
20. provider 성공 stdout과 timing은 packet cleanup 뒤에만 공개한다. 기존 실패가 있으면 cleanup 경고가 원래 종료 코드를 가리지 않는다.
21. 공개 redaction metadata는 허용된 음이 아닌 정수 count만 직렬화한다.
22. `Packet`은 렌더링 text/bytes/digest를 소유하고 receipt·launch가 재사용한다. user provider overlay는 canonical path·device·inode·mtime·size·언어로 캐시한다.
23. `--timeout` 생략 시 최종 packet bytes로 64KiB 이하 1200초, 128KiB 이하 1500초, 초과 1800초를 고른다. 명시값은 clamp하지 않는다.
24. receipt에 `timeout_seconds`/`timeout_source`/`timeout_applies`를 additive 공개하고 기존 `timing` 4-key 계약은 유지한다.
25. 새 packet은 directory advisory lock과 0600 lease marker를 process 동안 보유한다. 다음 실행은 current-user 0700 direct child 중 lock을 얻을 수 있고 marker가 24시간 지난 packet만 fd-relative로 비우며 marker는 마지막에 지운다.
26. task 범위 SIGTERM/SIGHUP은 각각 143/129 `SystemExit`로 바꾼다. spawn과 packet assignment 동안만 전달을 미루고 등록 직후 기존 BaseException 경로가 child group과 packet을 정리한다.
27. `--json` 실패는 같은 `packet-ask.v1`에 `ok: false`와 고정 code/kind/message만 넣는다. raw argv·예외 원문·경로·키·traceback은 넣지 않고 실제 exit code는 유지한다.
28. `inspect review|research`는 기존 scope·policy·redaction·budget·signal·cleanup 경계를 재사용하고 provider catalog/probe·credential·timeout·launch 없이 mode/selector/상대경로/count/bytes/digest만 공개한다.
29. builtin provider ID는 immutable adapter registry와 일치해야 한다. registry는 launch 함수 이름과 doctor 판정 종류만 코드로 보유하고 user alias는 adapter ID 없이 paste만 가능하다.
30. scrubbed 원문은 NFKC/Cf/dot/dash/decimal detection shadow에서 Unicode mailbox·phone을 추가 검사하되 shadow로 packet을 변형하지 않는다. 위치 mapping 없이 generic kind로 fail-close하고 Unicode 코드 operand+미인식 ASCII suffix는 오탐 완화를 위해 허용한다.
31. credential source는 immutable builtin backend registry의 env/keychain/prompt만 허용한다. auto 순서는 env→keychain으로 별도 고정하고 prompt를 포함하지 않으며 explicit source는 다른 backend로 fallback하지 않는다.
32. `_SECRET_VALUE_PATTERNS`는 primary scrub과 detection shadow verify가 공유한다. secret literal·URL userinfo·PEM header도 shadow에서 재검증하고 dotted 국내 mobile은 canonical scrub + mixed-separator fail-close로 처리하되 E.164 일반화는 별도다.
33. Kimi 성공 output은 session cleanup 뒤에만 반환한다. provider/output-guard/SystemExit/KeyboardInterrupt가 먼저 실패한 경로에서는 cleanup PacketAskError를 고정 warning으로 강등하고 primary exception을 bare raise로 보존한다.
34. 공유 KIMI_CODE_HOME의 config→launch→session cleanup 전체를 0600 non-inheritable advisory lock으로 직렬화한다. 획득은 30초 상한이며 lock 획득 오류만 confinement로 변환하고 with 본문 예외는 재분류하지 않는다.
35. `--preflight-timeout`은 기본 30초 absolute monotonic Deadline을 만들고 real-fd question stdin·rev-parse·name-status·diff·packet git init이 공유한다. 각 Git 호출은 `min(shared, now+30s)`를 쓰며 file read/CPU 단계의 강제 preemption은 주장하지 않는다.
36. user alias label/note는 길이·UTF-8 byte 상한과 Cc/Zl/Zp 및 bidi Cf 거절을 적용하되 ZWNJ/ZWJ는 허용한다. research diff selector는 policy와 scope collector 양쪽에서 거절하고 cache OSError는 path 없는 confinement로 변환한다.
37. task와 inspect는 `PacketInputs`로 question/policy를 먼저 확정하고 공통 packet pipeline context가 worktree·scope·budget·cache/GC·build 및 success/failure cleanup을 소유한다. provider lookup과 공개 output은 context 밖/본문에서 기존 순서를 유지한다.
38. inspect `--breakdown`은 cached scrub 결과로 question/framing/item byte와 항목별 public redaction count만 additive 공개한다. task `--progress`는 opt-in 30초 heartbeat로 fixed phase/elapsed만 0초 writable check 뒤 stderr에 쓰고 provider 종료 시 join한다.
39. 성공 표면은 자신의 한계를 같이 공개한다. receipt 와 inspect summary 에 코드 상수 `GUARANTEES`(leakage/vendor_training/vendor_local_copy/cwd_sandbox/redaction/doctor/policy_gate)를 additive 로 싣고, 사람이 읽는 receipt 한 줄에는 고정 축약을 붙인다. 산출값이 아니라 상수다. 부정문 키는 그래서 과잉 약속으로 드리프트하지 않는다. 기전 존재를 주장하는 redaction/doctor/policy_gate 는 상수가 기전보다 오래 살아남으면 기계 판독 가능한 거짓이 되므로 실제 동작 테스트에 묶는다. 목록은 전부가 아니다. 27의 실패 봉투는 넓히지 않는다. 실패는 아무것도 보내지 않은 상태라 상쇄할 잘못된 안심이 없다.
40. 영수증은 stderr 로 한 번 나가고 사라진다. 스킬 배포에서 스코프를 고르는 것은 MAIN 이므로 사람이 나중에 무엇이 나갔는지 물을 표면이 없다. `PACKET_ASK_LEDGER` 가 설정되면 런치 전에 append-only JSONL 한 줄을 남긴다. 절대 경로만 받고 워크트리 안·심링크·다른 uid 소유·비정규 파일을 거절한다. 워크트리 검사는 device·inode 조상 탐색이라 대소문자 비구분 파일시스템에서 뚫리지 않는다. O_APPEND·O_NOFOLLOW·O_NONBLOCK 으로 열고 이미 있던 파일이어도 첫 쓰기 전에 0600 을 강제하며 부분 쓰기를 끝까지 채운다. 기록은 egress 지점 도달을 뜻하지 전달 확인이 아니다. 질문과 파일 본문은 기록하지 않는다. 기록에 실패하면 벤더를 실행하지 않는다. 조용히 빠뜨리는 대장은 없느니만 못하다.
41. 설계는 사용자가 고른 패킷이지만 스킬 배포에서 `--files` 를 고르는 것은 MAIN 이다. 워크트리 루트의 사람이 커밋한 `.packet-ask-surface` 가 있으면 명시 파일 목록이 선언된 경로 접두어 안에 있어야 한다. 없으면 강제는 꺼져 있다. 매칭은 문자열이 아니라 경로 구성요소 단위이고, 절대 경로·상위 참조·글롭·제어문자·심링크·빈 선언을 거절한다. `--outside-surface` 로 우회할 수 있으나 receipt·inspect·ledger 에 `overridden` 으로 남는다. diff 경로도 검사한다. `--diff <임의 ref>` 는 워크트리 흔적 없이 과거를 꺼낼 수 있어 면제 논거가 성립하지 않는다. 하드링크와 diff 내용 주입은 잡지 못하며 선언은 경로에 관한 것이지 내용 보증이 아니다. 이것은 유출 방지 allowlist 가 아니라 공개 범위 선언이며, 에이전트가 범위를 넓히려면 커밋된 파일을 고쳐야 해서 그 편집이 git status 나 새 커밋으로 기존 사람 리뷰 루프 위로 올라온다는 것이 유일한 기제다.

42. `doctor` 는 성공 신호만 내는 표면이었다. `GUARANTEES` 는 성공한 task 의 receipt 와 inspect summary 에만 붙는데, 설치 절차가 `install-skills` 다음에 `doctor` 를 부르므로 사람이 이 도구를 믿을지 정하는 첫 화면은 `doctor` 다. 상쇄해야 할 신호보다 상쇄가 늦게 오면 안 된다. 코드 상수 `DOCTOR_SIGNALS`(verification/sandbox/signatures)를 프로바이더 줄 뒤에 append-only 토큰 한 줄로 낸다. 39 와 같은 이유로 산출값이 아니라 상수이며 기계 표면이라 언어 설정과 무관하다. 기전 존재를 주장하는 `verification: flags-mentioned` 는 실제 동작 테스트에 묶고, `sandbox: none` 과 `signatures: not-checked` 는 doctor 가 샌드박스를 만들거나 서명·해시를 확인하지 않는다는 소스 단언에 묶는다. 27 의 실패 봉투는 여기서도 넓히지 않는다.

43. `inspect` 는 provider·credential·timeout·launch 를 일부러 만지지 않는다. 그래서 "지금 무엇이 어디로 나가려 하는가" 한 장이 비어 있었다. task 의 `--preview` 는 같은 pipeline 으로 packet 을 만들고 검증한 뒤 영수증에 provider mode·credential source 종류·`--max-bytes` 잔여·`launch: not-started` 를 더해 출력하고 **벤더를 실행하지 않는다.** 자격증명은 존재만 보고 값을 읽지 않는다. 값을 읽으면 미리보기가 실행만큼 위험해진다. 대장은 남기지 않는다. 한 줄은 egress 지점 도달을 뜻하는데 미리보기는 거기에 도달하지 않으며, 나가지 않은 것이 섞이면 "무엇이 나갔나" 라는 대장의 질문이 무의미해진다. `--dry-run` 과 함께 쓰면 usage 거절이다. `--dry-run` 은 패킷 본문을 내고 `--preview` 는 절대 내지 않으므로 둘 중 하나를 조용히 고를 수 없다. 39 의 성공 표면 규약대로 `guarantees` 를 싣고 27 의 실패 봉투는 넓히지 않는다.

44. 추론 깊이는 노브가 없어 벤더 기본값에 맡겨져 있었다. 실측하니 같은 패킷·같은 질문에서 `low` 108초 `medium` 214초 `high` 444초 `max` 751초로 `high` 까지는 한 단계마다 약 2배(1.98, 2.07)였고 `max` 로는 1.69 로 완만해졌으며, 53KB 패킷에서도 `low` 146초 `max` 903초로 배수가 유지됐다. 반대로 effort 를 고정하면 바이트가 20배 늘어도 1.20~1.35배뿐이다. `--effort {low,medium,high,xhigh,max}` 를 연다. 열거값이라 argv 주입 표면이 없고 생략하면 플래그를 넣지 않아 벤더 기본값이 쓰인다. 값과 출처는 `timeout_seconds` + `timeout_source` 선례대로 나눈다. 고르지 않으면 `effort` 는 null 이고 `effort_source` 가 `vendor-default` 다. 한 필드에 섞으면 enum 도메인에 sentinel 이 들어가고 출처가 늘어날 때 값 도메인을 깨야 한다. auto timeout 은 크기 tier 와 effort tier 중 **큰 값**을 쓴다. effort 가 크기 tier 를 끌어내리면 큰 패킷 상한이 조용히 낮아지는 회귀다. low/medium 1200, high 1800, xhigh/max 2700 이며 관측 최악 903초에 3배 안팎 여유다. `xhigh` 는 재지 않았고 high 와 max 사이일 것이므로 보수적으로 max 와 묶었다. Claude 계열(glm, claude)만 이 노브를 갖고 paste·kimi 는 usage 로 거절한다. 조용히 버리지 않는다. effort 는 receipt·JSON·대장·`--preview` 에 전부 실린다. 39 의 성공 표면 규약이고 27 의 실패 봉투는 넓히지 않는다.

## 금지

구현·패치 적용, `--all`, 워크트리 밖, 커스텀 HTTP, 전역 `ANTHROPIC_BASE_URL` 변경, 워커 팜.

자세한 합의는 세션 설계(Claude/Codex/Grok)를 따른다.
