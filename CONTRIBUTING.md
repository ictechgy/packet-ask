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

## 공개 표면 선언

루트의 `.packet-ask-surface` 는 이 저장소가 자기 기능을 적용한 것입니다. SUB 에 보내도 된다고 사람이 선언한 경로 접두어만 적혀 있고, 에이전트가 `--files` 로 그 밖의 경로를 고르면 exit 11 로 거절됩니다. 유출 방지 allowlist 가 아니라 공개 범위 선언이며, 내용은 보증하지 않습니다.

- 범위를 넓히려면 이 커밋된 파일을 고쳐야 합니다. 그 편집은 git status 와 diff 에 남아 사람 리뷰 루프 위로 올라옵니다. 급하면 `--outside-surface` 를 쓰면 되지만 영수증에 `overridden` 으로 남습니다.
- 추적 파일을 새 최상위 경로에 추가하면 선언에도 추가하세요. `tests/test_project_meta.py` 가 "추적 파일 ⊆ 선언" 을 고정하므로 빠뜨리면 테스트가 실패합니다.
- 로컬 노트와 캐시(`HANDOFF.md`, `.serena/`, `.omc/`, `.venv/`, `dist/`)는 선언하지 않습니다. 선언하지 않는 것이 그 파일들이 패킷에 섞이는 것을 막는 실제 기제입니다.
- 리뷰용 diff·패킷 조각은 `.packet-ask-tmp/` 에 두세요. `.gitignore` 대상이면서 선언되어 있습니다. 저장소 루트에 만들면 선언 밖이라 거절됩니다.

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
