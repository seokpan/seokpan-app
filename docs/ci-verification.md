# Application CI 검증 명령과 실행 인계

[App #60](https://github.com/seokpan/seokpan-app/issues/60), [Roadmap #3](https://github.com/seokpan/seokpan-app/issues/3)의 A-09 첫 작업이다. Jenkins 연결은 [#40](https://github.com/seokpan/seokpan-app/issues/40), main Image Pipeline은 [#58](https://github.com/seokpan/seokpan-app/issues/58)에서 담당한다.

**Windows 로컬 검증 결과:** Backend·Frontend·Browser 여섯 단계와 같은 실행/소스의 최종 집계까지 확인했다. [PR #61](https://github.com/seokpan/seokpan-app/pull/61)과 아래 Windows 실행 결과 절은 같은 검증 대상 Commit과 실행 ID를 참조한다. 이 문서를 실제 Jenkins 실행 성공 기록으로 사용하지 않는다. Linux/Image/Harbor·실제 Provider는 미실행이며 A-09 후속과 A-10에서 검증한다.

## 고정 도구와 변경 범위

| 항목 | 고정값 | 이유 |
| --- | --- | --- |
| Backend | Python 3.13.15, uv 0.12.5 | 기존 pyproject.toml·uv.lock 유지 |
| Frontend | Node 24.19.0, npm 12.0.2 | 기존 실행 기준 유지. 전역 다른 npm 대신 호출한 npm 사용 |
| Format | Prettier 3.9.6 | 검사와 쓰기를 분리하며 소스·CSS·설정을 같은 포맷으로 관리 |
| Lint | ESLint 10.10.0, @eslint/js 10.0.1, typescript-eslint 8.70.0, globals 17.12.0 | JavaScript·TypeScript 권장 규칙. Node/browser 전역은 파일 역할별로 구분 |
| Coverage | @vitest/coverage-v8 4.1.11 | 기존 Vitest 4.1.11과 같은 버전. 제품 의존성은 바꾸지 않음 |

[Prettier 설치·검사](https://prettier.io/docs/install), [typescript-eslint 설정](https://typescript-eslint.io/getting-started/), [Vitest coverage](https://vitest.dev/guide/coverage.html)를 대조했다. npm 배포 메타데이터에서 Node/TypeScript/ESLint 호환 범위와 Vitest 정확 버전 peer 요구를 확인했다. 검사 도구 도입 당시에는 기존 Lock 버전을 유지했다. 이후 보안 재검증에서 확인된 `js-yaml` 한 항목만 `4.3.1 → 4.3.2`로 보완했다. 아래 보안 유지 절에 근거와 재검토 조건을 기록한다.

### js-yaml 보안 보완과 이후 유지

기존 main에도 있던 `openapi-typescript 7.13.0 → @redocly/openapi-core 1.34.19 → js-yaml 4.3.1`에서 [GHSA-2883-xcg3-v3hh](https://github.com/advisories/GHSA-2883-xcg3-v3hh)가 보고됐다. YAML merge 처리의 CPU 소모 문제이며 [수정 버전은 4.3.2](https://github.com/nodeca/js-yaml/releases/tag/4.3.2)다. Audit의 HIGH 2건은 해당 취약점과 의존하는 Redocly 항목의 집계다. 개발 도구 의존성이므로 검사에서 제외하지 않는다.

`frontend/package.json`의 `overrides`는 **`@redocly/openapi-core@1.34.19` 아래 `js-yaml: 4.3.2`**에만 적용한다. 직접 의존성과 Node/npm 버전은 유지하며 Lock의 해당 버전·배포 URL·integrity만 변경했다. [npm의 부모 버전별 override](https://docs.npmjs.com/cli/v12/configuring-npm/package-json#overrides)를 사용해 검증하지 않은 미래 Redocly에 같은 강제 변경을 자동 적용하지 않는다. 전역 설치나 `node_modules` 수동 편집으로 해결한 것이 아니다.

| 이후 상황 | 유지·검출 방법과 필요한 조치 |
| --- | --- |
| 같은 소스를 다시 설치 | package.json과 Lock을 함께 보존하고 고정 npm의 `npm ci`를 사용한다. 이번 fresh 설치에서 `npm ls js-yaml @redocly/openapi-core openapi-typescript`로 4.3.2 적용을 확인했다. Linux에서도 같은 Lock을 새로 설치해 확인해야 한다. |
| 현재 Lock에 새로운 취약점 공개 | `verify:ci`는 실행할 때마다 마지막 단계에서 별도 `npm audit`를 수행한다. Audit 비정상 종료는 전체 검사·최종 집계 실패이며 예전 성공 결과로 대신하지 않는다. Audit 실패 처리는 `frontend/scripts/frontend-ci.test.mjs`와 실제 실패 Run으로 확인했다. |
| Redocly 또는 OpenAPI 도구 갱신 | 부모 버전이 바뀌면 이번 override가 적용되지 않을 수 있다. 새 Lock과 `npm ls`로 실제 js-yaml 경로·버전을 확인하고 Audit·OpenAPI 타입·전체 회귀 검사를 다시 수행한다. 자동 `audit fix --force`나 Audit 기준 완화로 통과시키지 않는다. |
| 상위 도구가 안전한 js-yaml을 직접 요구 | 별도 의존성 변경에서 override 제거를 검토한다. 제거 후 재설치·Lock/설치 트리·Audit·OpenAPI·회귀 검사까지 통과해야 제거한다. 4.3.2를 영구히 안전한 버전으로 간주하지 않는다. |
| 설치만 성공하거나 Audit 서비스 접근 실패 | `npm ci`의 설치 성공을 보안 통과로 사용하지 않는다. 별도 Audit 완료와 정상 종료를 확인해야 한다. 통신 오류도 성공으로 바꾸지 않고 원인을 해소한 새 Run에서 검증한다. |

이는 현재 알려진 취약점을 수정하고 **검사를 실행할 때 재발을 검출하는 방식**이지 지속 감시 완료가 아니다. App main `6b5a50e`의 기존 Jenkinsfile에도 PR 검사에 별도 `npm audit`가 있다. 다만 새 검사·실행별 보고서 명령의 Jenkins 연결과 #58의 main 재검증·Image Pipeline은 아직 남아 있다. #40/#58 인계에서는 개발 의존성을 포함한 Audit 실행·실패 종료 코드 보존·실패 시 후속 Image Build/Push/GitOps 단계 중단을 확인해야 한다. 로컬 `npm run verify`나 Dockerfile의 `npm ci && npm run build`만 실행하는 것은 이 보안 검사를 대신하지 않는다. 작업·빌드가 없는 기간의 정기 검사나 알림은 별도 운영 합의가 필요하며 이번에 자동화·타인 Jenkinsfile을 변경하지 않았다. 알려지지 않은 취약점까지 검출할 수 있다는 보장도 아니다([npm Audit](https://docs.npmjs.com/cli/v12/commands/npm-audit), [npm ci](https://docs.npmjs.com/cli/v12/commands/npm-ci)).

Prettier 최초 적용은 기존 파일의 기계적 포맷 정리를 포함한다. 생성된 `src/api/schema.d.ts`는 기존 OpenAPI 생성 결과와 비교해야 하므로 포맷 대상에서 제외하고, package-lock.json도 npm이 관리한다. 문서·Dockerfile·nginx.conf·Jenkinsfile은 이번 Format 명령 대상이 아니다. Lint는 포맷 취향을 중복 검사하지 않으며 자동 `--fix`를 실행하지 않는다.

Lint 보완은 Board 키보드 이동 변수의 불필요한 초기값, 시험 Mock의 타입 표현, Room 응답에서 사용하는 필드 명시에 한정했다. 채팅의 Python `str.strip()` 호환 정규식은 의도적으로 제어문자를 포함하므로 해당 식에만 `no-control-regex` 예외 사유를 기록했다. 일반 입력 검사를 제거하거나 전체 규칙을 끄지 않았다.

## 현재 실행 가능한 명령

Backend 작업 디렉터리:

```text
python scripts/verify_ci.py --run-id <새 실행 ID> --uv <uv 실행 파일>
```

실행 Python과 uv의 정확 버전 확인 → Lock·Sync → Ruff Format/Lint → mypy → 전체 pytest → Room/Game/Vote 각각 branch coverage 100% → Runner 지정 시험 80% 순서다. 뒤 검사가 앞 실패를 덮지 않는다. 전체 시험과 Runner의 coverage 데이터/리포트도 분리한다. Alembic은 기존 pytest의 Model·Revision·Offline SQL 검사까지만 수행하고 실제 DB 변경 명령은 호출하지 않는다.

Frontend의 기존 로컬 검사 명령:

```text
npm ci
npm run verify
npm run test:ci -- --run-id <새 실행 ID>
npm audit
```

`verify`는 기존 로컬 자동 OpenAPI Export → Format Check → Lint → Type Check → 도구 Test → 기능 Test → Build다. 생성 타입을 자동 수정해 검사를 통과시키지 않는다. 로컬 자동 Export 및 기존 수동 `--schema` 입력은 유지하되, CI에서는 아래 실행 식별을 필수로 사용한다.

### Python → Node OpenAPI 인계

같은 Checkout을 공유하는 Python 단계에서, Backend 검사가 성공한 뒤 Backend 디렉터리를 작업 위치로 실행한다. Backend 실행기가 Run 디렉터리를 먼저 예약하므로 전체 실행은 반드시 Backend 검사 → OpenAPI Export → Frontend 검사 순서로 진행하고 같은 ID를 사용한다.

```text
python scripts/export_openapi_ci.py --run-id <Backend와 같은 실행 ID>
```

Python 3.13.15를 확인하고 `test-results/<실행 ID>/openapi/`를 새로 예약한 뒤 기존 Offline Export를 실행한다. 실제 Provider 설정은 전달하지 않으며 서버/DB를 기동하지 않는다. JSON 검사와 소스 변경 여부 대조까지 성공해야 `manifest.json`을 생성한다. 실패한 경로는 덮어쓰지 않고 새 ID로 다시 실행한다.

Node 단계에서는 Frontend 디렉터리에서 실행한다. Node 단계가 Python을 실행하지 않는다.

```text
npm run verify:ci -- --run-id <같은 실행 ID>
```

- `verify:ci`는 정확한 Node/npm 버전과 OpenAPI manifest를 확인한 뒤 `frontend-checks/`를 예약한다. `npm ci` → 같은 실행의 OpenAPI Type 차이 검사 → Format → Lint → TypeScript → 도구 시험 → `test:ci`와 동일한 coverage 시험 → Build → Audit 순서로 실행한다. 앞 단계가 실패하면 뒤 단계는 `not_run`으로 남기며 실패 코드와 실행 중 소스 변경 여부를 기록한다. 설치 전 외부 Node 모듈을 가져오지 않으므로 새 Node 작업 공간에서도 시작할 수 있다.
- `verify:ci`가 coverage 단계까지 수행하므로 같은 Run에서 별도 `test:ci`를 다시 실행하지 않는다. 개별 진단용 `verify -- --schema ... --run-id ...`는 유지하지만 전체 CI 순서·요약을 대신하지 않는다. 모든 단계가 끝난 뒤 아래 최종 집계를 수행한다.
- manifest의 실행 ID·Commit·소스 SHA-256·수정 여부·Python 버전 및 JSON SHA-256을 대조한 뒤 기존 API Type 차이 검사를 수행한다. `CI` 환경변수가 설정된 실행은 `--run-id` 없는 입력을 거부한다. `SEOKPAN_CI_RUN_ID`를 사용하는 Stage에서는 CLI ID도 일치해야 한다.
- 소스 해시는 `backend/`, `frontend/`, `.gitattributes`, `.gitignore`의 Git 관리 파일과 ignore되지 않은 미추적 파일의 실제 바이트를 대상으로 한다. 미커밋 변경·새 파일·삭제를 반영하며 Secret/의존성/시험 결과 등 ignore 대상은 수집하지 않는다. 해시만 기록하고 파일 내용·환경변수 값은 기록하지 않는다.
- 같은 Commit이라도 소스가 바뀌었거나 다른 Run 경로·수정된 JSON·누락된 manifest이면 실패한다. 링크로 다른 디렉터리를 가리키는 출력/소스도 허용하지 않는다. Python/Node 사이에 소스를 수정하거나 서로 다른 개행으로 다시 Checkout하지 않는다.
- 이 기록은 실수로 오래된 파일을 사용하는 것을 방지하기 위한 대조 자료이며 전자서명·보안 증명은 아니다. 서로 다른 실행에 같은 ID를 재사용하지 않는다.

`test:ci`는 Node/npm 정확 버전을 확인하고 새 Frontend 결과 디렉터리를 예약한 뒤 단일 worker·retry 0으로 Vitest coverage를 실행한다. 기존 개별 시험 시간 제한은 유지한다. 최초 전체 coverage 기준을 측정하며 임의의 전체 퍼센트로 합격 기준을 만들지 않는다. 검사 명령은 소스·Lock을 포맷하지 않는다. 개발자가 포맷을 적용할 때만 `npm run format`을 별도로 사용한다.

### Browser CI 실행

고정 Playwright Chromium 설치와 필요한 Linux 라이브러리를 먼저 준비한 뒤 Frontend 디렉터리에서 실행한다. 두 명령은 같은 Run ID를 사용할 수 있지만 같은 Stage를 재사용하지 않는다.

```text
npm run e2e:ci -- ui --run-id <Backend부터 사용한 같은 실행 ID>
npm run e2e:ci -- full --run-id <같은 실행 ID>
```

전체 CI 집계에서는 Backend부터 최종 집계까지 같은 실행 ID를 사용한다. Browser만 별도로 진단할 때는 새 ID를 사용할 수 있지만, 그 결과는 Backend/Frontend가 빠진 실행이므로 여섯 단계 전체 성공으로 집계되지 않는다.

각 명령은 Stage 예약/요약 → 전용 포트 사용 가능 여부 → 기존 Browser 실행 → 포트 종료 확인 → JSON/JUnit 및 실행 전후 소스 대조를 수행한다. Stage를 먼저 예약하므로 포트 충돌도 실패 요약으로 남는다. UI 18개와 전체 1개 흐름을 각각 두 번 실행하며 worker 1·retry 0을 유지한다. 전체 시험은 Memory Backend이며 실제 DB/Redis 연결 시험이 아니다.

Playwright 전체 제한은 UI 240초·전체 360초이며, 내부 정리 시간을 포함한 바깥 실행 제한은 각각 300초·420초다. Backend 검사 명령별·Frontend 검사 단계별·Vitest 바깥 제한은 각각 600초이며 기존 개별 시험 제한과 다르다. 제한을 초과하거나 실행기가 SIGINT/SIGTERM을 받으면 이번에 생성한 하위 명령을 정리하고 실패로 남긴다. OS 강제 종료·전원 중단은 정상 정리 완료를 보장하지 않는다. Backend는 시험 명령에 실제 `SEOKPAN_*` 설정을 전달하지 않고 `SEOKPAN_ENVIRONMENT=test`만 지정한다.

- Windows는 생성한 Child PID에만 `taskkill /PID <pid> /T /F`를 사용한다. 다른 Node/Python 프로세스나 포트 소유자를 검색해 종료하지 않는다. 관련 동작은 [Microsoft taskkill 설명](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/taskkill)을 따른다.
- POSIX는 생성한 명령의 별도 프로세스 그룹을 종료한다. [Node child_process 설명](https://nodejs.org/api/child_process.html)의 플랫폼 차이를 구분하며 실제 Linux 정리 시험은 아직 대기다. 도구가 별도 그룹으로 실행한 서버의 정리는 우선 Playwright 자체 제한/teardown에 의존하므로 Windows 결과를 Linux 결과로 전용하지 않는다.
- 포트가 실행 전 점유돼 있으면 실패하고 기존 소유자를 유지한다. 실행 후 포트가 남거나 OS 종료가 실패하면 성공으로 표시하지 않는다. 이 경우 대상 확인 없는 추가 프로세스 종료를 하지 않는다.

Frontend 보고서 검사는 XML을 파싱하고 JUnit 실제 Case 수·합계·실패/건너뜀, coverage JSON/XML의 측정 수, LCOV 형식을 확인한다. Browser JSON도 합계뿐 아니라 개별 결과·재시도·오류와 JUnit 수를 대조한다. 외부 XML Entity를 허용하지 않으며 Istanbul의 고정 Cobertura DOCTYPE은 읽어 올 자원이 아닌 선언으로 제거한다. 새 XML 의존성을 추가하지 않고 이미 고정된 jsdom을 사용한다. Backend도 JUnit 실제 Case·실패/건너뜀·합계와 coverage.py의 실제 행/분기 수를 대조한다. Domain/Runner 임계값은 검사 명령과 최종 집계 양쪽에서 확인한다.

### 최종 집계 — 실패한 실행도 수집

두 Browser 단계까지 종료된 뒤 Backend 디렉터리에서 실행한다. 앞 단계가 실패하면 나머지 검사를 무리하게 실행하지 않고 이 집계만 수행할 수 있다. Python 표준 라이브러리만 사용하므로 Frontend 설치 실패·Node 의존성 부재에도 결과를 정리할 수 있다.

```text
python scripts/summarize_ci.py --run-id <같은 실행 ID>
```

- `backend`, `openapi`, `frontend-checks`, `frontend`, `browser-ui`, `browser-full` 여섯 단계가 모두 통과해야 exit 0이다. `not_run`(단계 없음), `incomplete`(부분 생성/실행 중), `failed`(명령 실패), `invalid`(보고서 누락·불일치·내용 오류)를 구분하며 하나라도 있으면 exit 1이다.
- 실행 ID·Commit·소스 해시·수정 여부, Backend/Frontend 검사 단계의 정확한 목록과 종료 코드, OpenAPI SHA-256·형식, 실제 JUnit/coverage/Browser 결과를 재검사한다. 집계 중 소스나 읽은 보고서가 바뀌어도 실패한다.
- coverage.py의 Room/Game/Vote 각각 100%와 Runner의 행+분기 80%를 실제 XML로 재확인한다. Istanbul XML의 함수 진입 행은 소스 행 수에 중복 합산하지 않고, 전체 분기 수는 JSON/XML 요약끼리 대조한다. XML의 줄별 분기 표현을 전체 분기 데이터로 오인하지 않는다.
- `test-results/<run-id>/summary.json`을 한 번만 생성한다. 기존 최종 집계를 덮지 않으며 미완료 상태로 집계했다면 검사를 끝낸 뒤 같은 결과를 고쳐 쓰지 않고 새 Run을 수행한다. 읽은 보고서의 상대 경로·SHA-256을 기록하며 Raw 내용이나 Secret은 복사하지 않는다.
- 이 명령의 성공은 **오프라인 App 검사 묶음의 성공**이다. 실제 Linux Runtime·Image·Jenkins·Harbor·DB/Redis·MVP 인수 완료를 뜻하지 않는다. 실행하지 않은 항목은 최종 JSON에도 별도로 남긴다.

Jenkins 연결 시 각 검사 종료 코드를 유지하고 `post/always`에서 집계한 뒤 같은 Run의 보고서를 수집한다. 집계 자체를 실행하지 못했거나 최종 JSON이 없으면 미완료다. 집계 오류·Artifact 수집 오류가 원래 실패 코드를 지우거나 성공으로 바꾸면 안 된다. 이 문서는 인계 조건이며 팀원 소유 Jenkinsfile을 자동 수정하지 않는다.

## 출력 위치

저장소 루트의 `test-results/<run-id>/` 아래에 둔다. `backend/`와 `frontend/` Build Context 밖이며 Git 제외 대상이다. 생성할 Stage 디렉터리가 이미 있으면 재사용·삭제하지 않고 중단한다.

| Stage | 생성 파일 |
| --- | --- |
| 최종 집계 (Run 루트) | summary.json (여섯 단계 판정·읽은 파일 SHA-256·미검증 범위) |
| backend | summary.json, junit.xml, coverage.xml, runner-junit.xml, runner-coverage.xml |
| openapi | openapi.json, manifest.json (Export 성공 시에만 생성) |
| frontend-checks | summary.json (설치부터 Audit까지 실행·실패·미실행 기록) |
| frontend | summary.json, junit.xml, coverage/cobertura-coverage.xml, coverage/lcov.info, coverage/coverage-summary.json |
| browser-ui / browser-full | summary.json, results.json, junit.xml, artifacts/ |

단계 실패·미실행은 성공과 구분한다. Jenkins는 해당 실행 경로만 `post/always`에서 수집하고 원래 실패 코드를 유지해야 한다. `allowEmpty`만으로 성공을 선언하거나 이전 Run 보고서를 대신 가져오지 않는다. 실제 Credential/Cookie/CSRF 값·전체 환경변수는 수집하지 않는다. JSON/XML은 기존 도구의 합성 시험 결과이며 실제 Provider 자료가 아니다.

Browser CI 설정은 `SEOKPAN_CI_RUN_ID`가 가리키는 예약된 `browser-ui`/`browser-full` 디렉터리로 결과를 분리한다. UI는 5174, 전체 Memory E2E는 5175/8001을 사용하며 사용자 5173/8000을 재사용하지 않는다. UI는 Python이 필요 없고, 전체 E2E는 Backend .venv의 Python 3.13.15가 필요하다. 양쪽 모두 Playwright 1.63.0의 고정 Chromium Headless Shell을 사용하며 개인 Chrome/Edge 프로필에 접속하지 않는다. Browser 요약에도 Node 버전·실행 OS를 기록한다.

## Linux·Jenkins 담당 경계

실행환경 준비 조건은 다음과 같다. 이 목록은 새 이미지 선택이나 실제 배포 승인이 아니다.

| 실행 위치 | 필요한 준비 |
| --- | --- |
| Python 검사·Export·최종 집계 | Python 3.13.15, uv 0.12.5(검사), Git 명령, 같은 App checkout. 집계는 별도 Python 패키지 설치 없이 실행하지만 소스 확인에 Git이 필요함 |
| Node 검사 | Node 24.19.0·npm 12.0.2·Git, 기존 Lock 설치/Audit에 필요한 패키지 저장소 접근. Node 컨테이너에서 Python Export를 다시 실행하지 않음 |
| Browser | 기존 Playwright 1.63.0에 대응하는 Chromium Headless Shell 및 지원 Linux 시스템 라이브러리. 전체 시험에는 같은 Linux 환경에서 만든 Backend .venv도 필요함 |
| 공유 작업 공간 | 단계 사이 같은 경로·소스·LF 유지, 각 실행 사용자에게 필요한 .venv/node_modules/cache/test-results 쓰기 권한. 사용자 권한 차이를 전체 777 권한으로 우회하지 않음 |

Windows의 .venv나 node_modules를 Linux로 복사해 재사용하지 않는다. Linux에서 같은 Lock으로 다시 설치하고 새 Run ID를 사용한다. Jenkins Checkout 컨테이너에 Git이 있다는 사실만으로 Python·Node 검사 컨테이너에도 Git이 있다고 가정하지 않는다. 설치 캐시·도구 이미지가 준비되지 않으면 별도 환경 작업으로 처리하며 검사 중 시스템 패키지나 전역 도구를 임의 설치하지 않는다.

- App은 검사·보고서·오류 종료와 필요한 도구/경로를 제공한다. Jenkins Stage/Report 수집·Agent 구성은 기존 담당 작업에서 연결한다.
- 현재 GitOps Node Agent는 Alpine 이미지다. Browser용 Linux 라이브러리나 Backend Python이 있다고 가정하지 않는다. Browser용 지원 Linux 실행환경·고정 이미지 준비는 별도 협의/승인 후 검증한다.
- 비공개 CI 이미지의 Kubelet imagePullSecrets 참조와 PR 코드가 읽는 Harbor Credential Mount는 다르다. PR 검사 컨테이너에는 Harbor/GitOps/실제 DB·Redis Credential을 노출하지 않는다. Secret 내용은 조회하지 않는다.
- GitOps `0f5bfc1`의 `cicd/jenkins-jcasc-configmap.yaml`에는 PodTemplate 수준으로 `harbor-robot-dockerconfig` Secret Volume이 선언돼 있다. 이 명령을 기존 Agent에 연결하기 전, 실제 PR Pod의 검사 컨테이너에 해당 Mount·Credential이 전달되지 않는 구성을 담당자가 확인해야 한다. 정적 선언만으로 실제 노출 여부를 단정하지 않으며, 검사 명령이 Credential을 사용하지 않는 것만으로 격리가 완료됐다고 보지 않는다. 현재 Jenkinsfile의 개별 검사를 새 명령으로 연결하고 실행별 보고서를 수집하는 작업도 #40/#58에 남아 있다.
- [Infra PR #157](https://github.com/seokpan/seokpan-infra/pull/157)은 main `bbfc842`에 병합됐다. Candidate `scan-*` 예외 정책을 #58이 소비하며 App 검사 구현을 차단하지 않는다. [Infra #158](https://github.com/seokpan/seokpan-infra/issues/158)의 API Robot은 별도 후속이다.
- [Infra PR #159](https://github.com/seokpan/seokpan-infra/pull/159)은 main `0d219b1`에 병합됐고 공급 작업 [#150](https://github.com/seokpan/seokpan-infra/issues/150)은 종료됐다. Runtime/Migration Secret 공급·회전 검증은 담당자 실행 보고다. App #22/#50의 실제 연결 성공으로 사용하지 않는다. A-09 검사는 이 자격증명이 필요하지 않다.

## 실제 Image Smoke에서 확인할 항목 — 미실행

| 대상 | 확인 내용 |
| --- | --- |
| Backend | 기존 Dockerfile의 Python/uv·Lock, UID/GID 10001, Uvicorn 8000, /health/live 및 종료 처리 |
| Backend 읽기 전용 실행 | 필요한 임시 쓰기 경로만 별도 Mount. 알 수 없는 쓰기 오류를 권한 전체 해제로 우회하지 않음 |
| Migration 자산 | /app/alembic.ini·migrations 및 audit 자산 포함, 기본 CMD에서 Migration 자동 실행 없음 |
| Frontend | 기존 Nginx UID 101·8080·/health/live, /tmp 쓰기 경로와 읽기 전용 Root FS |
| 정적 제공 | SPA 새로고침, index.html 재검증·해시 assets Cache, API/WS 경로를 HTML로 오인하지 않는 Gateway 통합 |
| 결과 판정 | non-production Process Smoke와 실제 DB/Redis readiness를 분리. Production에서 Memory Provider를 허용해 통과시키지 않음 |

이 목록은 Image 실행 성공 기록이 아니다. Container/Image/Harbor 작업은 실제 환경·명령과 대상 승인을 받아 수행하고, 실제 Provider·다중 Replica·Gateway는 A-10에서 검증한다.

## Windows 실행 결과 — 2026-09-09

### 최종 전체 검증

기반 main은 `6b5a50e6a5b4d98273643f442bd9269a6b51674c`다. 아래 표는 보안 보완을 Commit한 뒤 깨끗한 작업 트리에서 전체 검사를 새로 실행한 결과이며, [PR #61](https://github.com/seokpan/seokpan-app/pull/61)의 최종 검증 표와 같은 실행을 가리킨다.

| 실행 식별 | 값 |
| --- | --- |
| 검증 대상 Commit | `156c282eab10ec6cb21926287d472792ebeead7e` |
| 실행 ID | `a09-pr-final-20260909-01` |
| 소스 SHA-256 | `5ca80e13e32904d8b82761fb2717871a4680fa399b00ee7d5895584a14c34761` |
| 실행 당시 수정 여부 | `dirty false` |
| 실행환경 | Windows, Python 3.13.15·uv 0.12.5·Node 24.19.0·npm 12.0.2 |
| 최종 판정 | 6 Stage `passed`, `exit_code: 0`, 보고서 19개 해시·실행/소스 일치 |
| 원본 결과 위치 | `test-results/a09-pr-final-20260909-01/summary.json` 및 같은 실행 디렉터리의 보고서 |

이 절을 보완하는 문서 전용 Commit과 위 검증 대상 Commit은 구분한다. 위 수치를 문서 보완 후 HEAD에서 새로 실행한 결과로 표현하지 않는다. 소스 해시와 `dirty`의 검사 범위는 앞서 명시한 `backend/`, `frontend/`, `.gitattributes`, `.gitignore`이며, 문서만 변경됐는지는 별도로 Git Diff 전체를 대조한다. 해시 일치만으로 모든 파일이 같다고 판단하지 않는다.

이후 전체 검증을 새로 실행하면 새 실행 ID를 사용하고, PR과 이 절의 Commit·실행 ID·소스 해시·결과를 함께 갱신한다. 이전 결과는 이력으로 보존하며 기존 manifest/summary의 Commit이나 결과를 수정해 새 실행처럼 재사용하지 않는다.

| 검사 | 결과 |
| --- | --- |
| Backend Lock·Sync·Ruff·mypy | PASS |
| Backend 전체 pytest | 912 PASS, 실패/오류/건너뜀 0 |
| Room·Game·Vote Domain | 각각 행·분기 100% |
| Turn Runner 지정 시험 | 17 PASS, 행+분기 86.65% (기존 80% 기준) |
| Offline OpenAPI → Frontend 타입 대조 | PASS |
| Frontend Format·Lint·TypeScript·Build·Audit | PASS, Audit 취약점 0개 |
| Frontend 도구 / 기능 시험 | 79 PASS / 257 PASS |
| Frontend coverage | Statements 92.07%, Branches 89.62%, Functions 93.75%, Lines 94.12% |
| UI Browser / Memory 전체 Browser | 36 PASS / 2회·총 6판 PASS, worker 1·retry 0 |
| 최종 집계 | 6 Stage passed, exit 0, 19개 보고서 해시·실행/소스 일치 |

Backend 전체 912개에는 Python 도구 시험 90개가 포함돼 있으며 모두 PASS다. `backend/tests/tooling/`과 `frontend/scripts/*.test.mjs`에서 경로 이탈·잘못된 버전·명령 시작 실패·검사 실패·시간 초과·보고서 누락/불일치·집계 중 소스 변경 등을 검증한다. 실행기 Test Double과 실제 프로세스 시험을 구분하며, 실제 Windows 하위 프로세스 timeout에서는 해당 부모/자식만 종료되고 대조 프로세스는 유지됐다. Linux 신호/정리는 아직 직접 실행하지 않았다.

### 이전 검증·실패 이력

`a09-aggregate-20260909-01` 성공 이후 Commit 직후의 `a09-commit-219a2fd-20260909-01`에서 Audit HIGH 2건·최종 exit 1이 발생했다. 해당 실행의 Backend/Frontend 기능은 통과했지만 Browser는 미실행이며 원격 반영을 중단했다.

이후 `a09-security-20260909-01`은 Commit `219a2fd3a41c6f2a108abc2afc065c872a8364df` 위에 js-yaml 보완을 적용한 미커밋 작업 트리의 검증이다. 소스 SHA-256은 `5ca80e13e32904d8b82761fb2717871a4680fa399b00ee7d5895584a14c34761`, `dirty true`이며 설치부터 여섯 단계 모두 통과했다. 이는 위 최종 clean-commit 실행 이전의 결과다. 이전 Audit 0개 응답이나 실패 Run은 삭제하지 않으며 최종 결과와 구분한다.

PR 전 재검토에서 제한된 Windows 실행 권한으로 도구 시험을 다시 실행했을 때 Python 89 PASS/1 FAIL, Node 78 PASS/1 FAIL이 발생했다. 두 실패 모두 `taskkill` 종료 단계였다. 동일 소스를 시험 프로세스 종료가 허용된 실행에서 다시 검증해 Python 90 PASS·Node 79 PASS, 건너뜀 0개를 확인했다. 최초 실패를 숨기거나 제한된 환경에서도 정리가 성공한다고 주장하지 않는다. 실행환경에는 자신이 시작한 하위 프로세스를 종료할 권한이 필요하며 이 결과가 Linux 실행 권한·정리를 보증하지 않는다.

실패 경로를 확인한 실제 별도 실행:

- `a09-aggregate-incomplete-20260909-01`: OpenAPI만 생성. 나머지 5 Stage not_run·최종 exit 1.
- `a09-browser-port-conflict-20260909-01`: UI 시험 포트 점유 시 기존 시험 서버를 보존하고 기동 전 거부.
- `a09-browser-timeout-20260909-01`: 전체 Browser의 의도적 10초 제한으로 실패/미완료가 기록됐고 5175/8001이 반환됨. 정상 420초 바깥 watchdog을 기다린 시험은 아님.

Raw 결과는 각 `test-results/<run-id>/`에 보존하며 Git에는 넣지 않는다. 최종 성공 Run 후 5174/5175/8001 반환을 확인했다. 실제 사용자 5173/8000·개인 Browser 프로필을 재사용하지 않았다. 반복 전환 중 기존 Vite `ECONNABORTED` 및 NO_COLOR/FORCE_COLOR 경고를 관측했으며 기능/page-error 단언과 결과 JSON/JUnit은 통과했다. 경고를 숨기거나 원인이 완전히 규명됐다고 주장하지 않는다.

변경 범위 대조에서 기존 Frontend 파일 62개는 고정 Prettier 포맷을 수렴시킨 결과와 일치했다. 그 밖의 제품 코드 보완은 Board 키보드 변수 초기값 정리, Room 응답 필드 명시, 채팅 정규식의 국소 Lint 예외다. GamePanel 시험 조회 최적화와 Mock 타입 정리도 포함하며 기존 시간 제한과 기능 단언은 유지한다. 최초 도구 도입은 기존 Lock 버전 변경 0개·새 항목 112개였으며, 후속 보안 보완을 포함한 최종 차이는 기존 js-yaml 1개 패치 변경·112개 추가다. 이 대조는 실제 회귀 시험을 대신하지 않는다.
