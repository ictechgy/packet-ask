# CLAUDE.md

이 저장소의 에이전트 지침은 [AGENTS.md](AGENTS.md) 하나로 관리한다. 먼저 그
파일을 읽는다.

하위 디렉터리에서 작업할 때는 그 서브트리의 `AGENTS.md` 가 함께 적용된다.

- [src/packet_ask/AGENTS.md](src/packet_ask/AGENTS.md) — 구현 불변식, egress
  표면 구분, 값과 출처 분리, 요청과 기본값, 가드를 여는 규칙, 출력 계약,
  메시지 카탈로그, 종료 코드.
- [tests/AGENTS.md](tests/AGENTS.md) — TDD 순서, 결로 고정과 양성 대조,
  계약 테스트, 수집·캐시 함정.
- [docs/AGENTS.md](docs/AGENTS.md) — 설계 불변식 번호, 과잉 약속 금지,
  근거를 쓰기 전에 측정하기, 영/한 parity 와 그 한계.
- [.github/AGENTS.md](.github/AGENTS.md) — CI 핀, 릴리스,
  Trusted Publishing, 배포 뒤 실측.

지침을 고칠 일이 생기면 이 파일이 아니라 해당 `AGENTS.md` 를 고친다. 이
파일은 포인터로만 둔다.
