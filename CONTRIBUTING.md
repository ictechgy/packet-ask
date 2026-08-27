# 기여

이 저장소는 개인 코딩 구독으로 보내는 면적을 줄이는 로컬 CLI입니다. 동작 변경 전에 테스트를 먼저 고치거나 추가하세요.

## 개발

```bash
uv sync --group dev
uv run pytest
```

- 기본 브랜치에 직접 커밋하지 않습니다. `feature/` `fix/` `refactor/` 브랜치에서 작업합니다.
- 커밋은 Conventional Commits, 본문은 한국어입니다.
- 한 커밋에 문서와 동작 변경을 섞지 않습니다.
- 시크릿·키·실패킷·`.env` 를 커밋하지 않습니다. 변수 이름은 `.env.example` 만 참고합니다.

## 범위

구현은 스크럽된 패킷과 공식 CLI 원샷에 머뭅니다. 커스텀 HTTP 클라이언트, 전역 `ANTHROPIC_BASE_URL` 변경, 워커 팜은 받지 않습니다.
