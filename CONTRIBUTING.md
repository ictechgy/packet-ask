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
- 시크릿·키·실패킷·`.env` 를 커밋하지 않습니다. 변수 이름은 `env.example` 만 참고합니다.

## 범위

구현은 스크럽된 패킷과 공식 CLI 원샷에 머뭅니다. 커스텀 HTTP 클라이언트, 전역 `ANTHROPIC_BASE_URL` 변경, 워커 팜은 받지 않습니다.

## PyPI 배포

업로드는 GitHub Actions Trusted Publishing 만 사용합니다. 장기 PyPI 토큰을 저장소에 두지 않습니다.

1. GitHub 저장소 Settings → Environments 에 `pypi` 환경을 만듭니다.
   - Deployment branches/tags 는 **All** 이거나 `v*` 태그여야 합니다. `main` 만 허용하면 태그 릴리스가 거절됩니다.
2. [PyPI pending publisher](https://pypi.org/manage/account/publishing/) 에 다음을 등록합니다.
   - PyPI project name: `packet-ask` (`pyproject.toml` 의 `name` 과 바이트 단위로 같아야 합니다)
   - Owner: `ictechgy`
   - Repository: `packet-ask`
   - Workflow filename: `release.yml` (경로 없이 파일명만)
   - Environment: `pypi`
3. pending publisher는 **첫 업로드 전까지 이름을 예약하지 않습니다.** 등록 직후 `https://pypi.org/project/packet-ask/` 가 404인지 확인하고 태그를 밉니다.

```bash
git tag -a v0.1.1 -m v0.1.1
git push origin v0.1.1
```

태그 `vX.Y.Z` 의 `X.Y.Z` 는 `pyproject.toml` 버전과 같아야 합니다. 워크플로가 `uv build` 후 `uv publish` 합니다.
