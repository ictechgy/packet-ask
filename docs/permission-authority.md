# 저장소 권한 검사 운영

`.github/workflows/permission-authority.yml`은 main의 검사기로 PR 병합 tree를
검사하고 별도 GitHub App으로 `permission-authority`를 발급한다. 저장소 CLI의
런타임 기능은 아니다. 서버 강제는 아래 설치·검증·보호 설정까지 완료해야 성립한다.
설치 현황과 실제 실행 증거는 로컬 HANDOFF에 남긴다.

## 신뢰 경계

- `workflow_run`은 실제 CI workflow ID와 PR head를 GitHub API로 확인한다.
  checkout은 `github.workflow_sha`의 main 코드만 사용한다. PR의
  `refs/pull/N/merge`를 별도 Git 객체 저장소에 받아 API의 병합 SHA와 비교한다.
- PR의 `merge_commit_sha`를 제공하는 REST API `2022-11-28`을 명시한다.
  `2026-03-10` 응답에서는 해당 필드가 없음을 실제 API로 확인했다. 버전을 바꿀 때는
  응답 계약과 병합 SHA 결속을 함께 이관한다. [GitHub 지원 일정](https://docs.github.com/en/rest/about-the-rest-api/api-versions)에
  따른 현재 버전 지원 종료일은 2028-03-10이다. 필드 누락을 다른 SHA로 대체하지 않는다.
- 후보는 raw blob으로만 복원한다. Git checkout, 후보 모듈 import, 테스트 실행,
  후보 패키지 설치, 후보 artifact/cache 복원은 하지 않는다. 링크·gitlink·비ASCII
  경로·비밀 파일 경로·대소문자 충돌과 파일/바이트 예산 초과는 거절한다.
- 외부 Python 격리 모드의 ExitZero 0.6.1 wheel을 해시로 고정한다. 허용 정책은
  `exitzero_verify`와 `src/**/*.py`의 `python.syntax`뿐이다. 명령·다른 plugin을
  추가하는 정책은 운영자 승인으로도 통과시키지 않는다.
- App은 packet-ask 한 곳에 Contents read, Checks write, Metadata read만 가진다.
  private key는 `exitzero-authority` Environment에 두며 deployment branch policy는
  정확히 `main` 타입 `branch` 하나다. repository secret으로 옮기지 않는다.
- App 토큰은 후보 검사 뒤 발급하고 작업 종료 때 폐기한다. 기본 Actions 토큰은
  읽기 전용이다. 검사 영수증에는 PR/base/head/merge/정책 해시를 남긴다.

## 변경 승인

editable 변경은 기준 정책으로 검사한다. protected 변경에는 `protected`,
immutable·미분류 변경에는 `governance` 운영자 판단이 필요하다. governance도
후보 정책이 모든 경로를 분류하고 고정된 안전 검사에 통과해야 한다. 이 절차는
로컬 승인 파일이나 모델 설명을 권한으로 취급하지 않는다.

1. PR diff와 권한 검사 artifact의 `analysis.json`을 독립 리뷰한다. API로 현재
   PR의 head SHA, base SHA, synthetic merge SHA를 확인한다. 후보 정책의 SHA-256은
   `inspection.candidate_policy`를 사용한다.
2. 저장소 소유자가 main의 `Permission authority` 워크플로를 **새로** 실행한다.
   `pr`, `mode`, `head`, `base`, `merge`, `policy`에 검토한 값을 넣는다.
   기존 실행의 Re-run은 승인으로 허용하지 않는다.
3. 워크플로는 소유자의 숫자 ID, 이벤트 sender, 실행 시도 1회, main ref를 확인한다.
   최신 PR 값과 검토 값이 하나라도 다르면 App 승인을 쓰지 않는다.
4. 성공한 승인도 별도 App의 `permission-authority-approval` 검사에 정확한
   repository/PR/base/head/merge/두 정책 해시/mode를 묶는다. 자동 재검사는 이
   발급 주체와 전체 결속 값을 확인한다. 새 커밋이나 main 변경에는 재승인이 필요하다.

PR별 작업은 직렬화한다. 최종 검사는 같은 결속 값의 기존 기록을 갱신한다.
권한 실패·구문 실패·운영 오류를 neutral/skipped 성공으로 바꾸지 않는다. 충돌,
삭제된 PR, 오래된 main 검사기 등 검사 불가능 상태는 필수 성공 증거를 만들지 않는다.

## 설치와 검증

1. 전용 private App을 위 권한으로 생성하고 packet-ask만 선택해 설치한다.
2. main 전용 Environment를 만들고 후보 branch/PR에서 해당 환경의 job이 실행되기
   전에 거절되는지, 비밀값 없이 확인한다.
3. 환경 secret `EXITZERO_AUTHORITY_PRIVATE_KEY`와 공개 환경 변수
   `EXITZERO_AUTHORITY_APP_ID`, `EXITZERO_AUTHORITY_CLIENT_ID`를 등록한다.
4. 기존 두 필수 CI를 유지한 채 워크플로를 main에 반영하고 advisory 상태로 검증한다.
   정상 소스 변경, 테스트/CI/정책 변경 거절, 정확한 운영자 승인, 새 커밋의 승인
   무효화, GitHub Actions가 같은 이름으로 쓰는 가짜 성공을 각각 확인한다.
5. 검증된 `permission-authority`를 **전용 App ID**에 고정해 main 필수 검사에
   추가한다. 기존 Python 3.11·3.13 검사, 최신 main 요구, 관리자 적용을 유지한다.
   서버 설정을 다시 읽어 발급 주체까지 확인한다.

App 설치만으로 서버 강제가 켜지지 않는다. GitHub Actions 발급 검사 이름만
추가한 설정도 별도 권한 장벽이 아니다. App 발급 검사와 main 보호 설정을 함께
확인해야 한다. 장애 때는 운영 원인을 고치고 새 검사를 실행하며, 가짜 성공이나
관리자 우회로 해결하지 않는다.

## 한계

이 검사는 정적 경로 권한과 Python 구문만 판단한다. 코드의 의미적 정확성,
테스트의 충분성, 데이터 유출 방지, OS 격리를 증명하지 않는다. 기존 CI와 독립
리뷰가 계속 필요하다. 소유자 계정, GitHub 플랫폼, main 검사기, 고정된 공급망과
Environment 정책은 신뢰 기준이다. 관리자 권한 탈취를 막는 기능은 아니다.

검사 기록은 synthetic merge SHA에 발급하므로 base/head 변경 뒤 기존 성공을
재사용할 수 없다. GitHub 서버의 실제 필수 검사 선택과 차단 동작은 설치 때 별도로
실측해야 한다. 로컬 pytest와 모의 API 결과만으로 hosted enforcement를 주장하지 않는다.
