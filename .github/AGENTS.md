# CI·릴리스 규약

`.github/` 아래에서 활성화된다. 루트 [AGENTS.md](../AGENTS.md) 를 먼저 읽는다.

## 액션 핀

모든 `uses:` 는 **커밋 SHA 로 핀**하고 뒤에 `# vX.Y.Z` 주석을 단다. 태그
참조로 바꾸지 않는다. 메타 테스트가 이것을 고정한다.

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
```

## 권한

- 기본 토큰은 읽기 전용이다.
- `id-token: write` 는 **업로드 잡에서만** 연다. 빌드 잡에 주지 않는다.
- 빌드와 업로드를 별도 잡으로 나누고 `needs:` 로 잇는다. 업로드 권한이 빌드
  잡으로 새지 않게 하는 것이 이 분리의 이유다.

## PyPI 업로드

- **Trusted Publishing(OIDC)만** 쓴다. 장기 토큰을 저장소에 두지 않는다.
  `PYPI_API_TOKEN` 이나 `TWINE_PASSWORD` 가 들어오면 메타 테스트가 실패한다.
- GitHub `pypi` 환경을 쓴다. Deployment branches/tags 는 **All** 이거나 `v*`
  여야 한다. `main` 만 허용하면 태그 릴리스가 거절된다.
- PEP 740 attestation 을 생성한다.

## 언제 배포하는가

**에이전트 지침 수정은 배포하지 않는다.** `AGENTS.md` 와 `CLAUDE.md` 는 기여자
문서이지 런타임 데이터가 아니다. 머지로 끝이고 버전을 올리지 않는다.

`src/packet_ask/AGENTS.md` 는 패키지 디렉터리 안이라 기본값으로는 wheel·sdist
에 실렸다. 그래서 지침만 고쳐도 배포물이 바뀌어 매번 "배포해야 하나" 를 다시
판단하게 됐다. 실제로 0.6.0 때는 배포하지 않고 0.7.1 때는 배포해 갈렸다.
`pyproject.toml` 의 `[tool.uv.build-backend] source-exclude` 로 빼서 그 판단이
필요 없게 만들었다. 메타 테스트가 설정과 **실제 산출물**을 둘 다 고정한다.

`src/packet_ask/data/SKILL.md` 는 다르다. `install-skills` 가 쓰는 런타임
데이터이므로 반드시 실린다. 스킬을 고쳤으면 배포해야 사용자에게 간다.

배포하는 것: 동작 변경, `SKILL.md`, 사용자 대면 메시지, README·SECURITY 처럼
패키지 메타데이터에 들어가는 문서.

## 릴리스 절차

1. 동작·문서 PR 을 먼저 다 머지한다.
2. `chore: 버전을 X.Y.Z로 올린다` 만 담은 별도 PR 을 만든다. 다른 변경을
   섞지 않는다.
3. 머지 뒤 태그를 단다. **로컬 `main` 에 커밋하지 않는다.**

   ```bash
   git fetch origin
   git tag -a vX.Y.Z -m vX.Y.Z origin/main
   git push origin vX.Y.Z
   ```

4. 태그 `vX.Y.Z` 의 `X.Y.Z` 는 `uv version --short` 와 같아야 한다.
   워크플로가 먼저 이것을 검사하고 다르면 실패한다.
5. `Publish release to PyPI` 워크플로가 build → 두 smoke → attestation →
   publish 를 모두 통과했는지 확인한다.
6. `https://pypi.org/pypi/packet-ask/X.Y.Z/json` 으로 게시를 확인한다.

## 배포 뒤

```bash
uv tool install "packet-ask==X.Y.Z" --force --refresh
packet-ask install-skills --force
```

**설치될 때까지 재시도한다.** `uv tool upgrade` 는 exact pin 때문에 먹지 않고,
`--force` 만으로는 PyPI 인덱스 캐시 때문에 옛 버전이 다시 깔린다. `--refresh`
가 그 로컬 캐시를 버린다.

그런데 배포 **직후에는** `--refresh` 도 부족하다. PyPI 의 JSON API 가 새 버전을
보여준 뒤에도 uv 가 쓰는 simple index 는 아직 반영 전일 수 있다. 이때
`@latest` 는 옛 버전을 깔고, 버전을 명시하면 `no version of packet-ask==X.Y.Z`
로 아예 실패한다. 둘 다 실측했다. **버전 명시가 더 확실한 것이 아니라, 실패가
조용하지 않다는 점만 다르다.** 그래서 명시하는 편을 권한다.

전파는 대개 곧 끝난다. 실패하면 잠시 뒤 같은 명령을 다시 돌리고,
`uv tool list` 가 새 버전을 보여주는 것으로 확인한다.

설치 뒤 배포본으로 이번 배치의 기능을 **하나씩 실행해 본다.** 로컬에서
통과한 것과 배포된 wheel 이 같다는 보장은 build·smoke 까지이고, 실제 CLI
표면은 별개다.

## 함정

- PEP 740 attestation 여부를 PyPI JSON 의 `provenance` 필드로 판정하지 말 것.
  성공한 릴리스에서도 `null` 이다. `https://pypi.org/integrity/packet-ask/
  X.Y.Z/<파일명>/provenance` 가 200 인지로 본다.
- push 직후 `gh pr merge` 가 `Head branch is out of date` 로 실패하는 것은
  GitHub 이 mergeability 를 다시 계산하는 동안의 일시적 현상이다. 40~60초
  뒤 재시도한다.
- PyPI pending publisher 는 첫 업로드 전까지 프로젝트 페이지가 404 다.
