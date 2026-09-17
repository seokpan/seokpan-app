# Production Runtime 조립·검증 경계

이 문서는 Production Backend가 MariaDB·Redis Provider를 조립하는 코드 계약과 실제 Kubernetes 실행 순서를 정의합니다. Secret 값, 실제 Database URL 또는 Credential은 기록하지 않습니다.

## 실행 진입점

Container는 기존과 같이 `uvicorn seokpan.app:app`을 실행합니다. `SEOKPAN_ENVIRONMENT=production`이면 import 단계에서는 외부 연결을 만들지 않는 Shell을 반환하고, ASGI lifespan이 시작될 때만 Provider를 생성합니다. local·test·development는 기존 Memory 조립을 유지합니다.

Production의 필수 설정 이름은 다음과 같습니다.

- `SEOKPAN_ENVIRONMENT=production`
- `SEOKPAN_INSTANCE_ID`: Pod별 고유 Instance ID
- `SEOKPAN_IDENTITY_DATABASE_URL`: `identity_svc` Runtime 계정
- `SEOKPAN_GAME_DATABASE_URL`: `game_svc` Runtime 계정
- `SEOKPAN_DATABASE_CA_FILE`: 읽을 수 있는 내부 CA 파일
- `SEOKPAN_REDIS_URL`: `redis://redis.platform.svc.cluster.local:6379/0`
- `SEOKPAN_ALLOWED_ORIGINS`: JSON 배열로 표현한 허용 Origin

정상 Backend는 `db_admin` Migration Credential을 사용하지 않습니다. URL·CA·Credential 값은 Kubernetes Secret/ConfigMap과 Mount로만 공급하고 로그·문서·Git에 남기지 않습니다.

## 시작·준비·종료

시작 순서는 역할별 MariaDB Engine → Redis Client → 두 DB의 `SELECT 1` → Redis `PING` → 전체 Service/Runner 조립입니다. 필수 Runner가 시작된 뒤에만 Runtime 요청을 전달하고 `/health/ready`를 200으로 엽니다. 설정 오류, Provider 연결 실패 또는 Runner 조기 종료는 준비 완료로 오인하지 않습니다.

종료 시 readiness를 먼저 내리고 WebSocket Runtime을 종료한 뒤 Runner를 취소·회수합니다. 이후 Redis Client와 두 DB Engine을 닫습니다.

## 실제 환경 적용 순서

이 Source PR의 synthetic 시험을 실제 Provider 성공으로 확대하지 않습니다. 병합 후 다음 Gate를 순서대로 통과해야 합니다.

1. 새 `main` Commit의 Backend Image Build·Scan·Health Smoke를 통과하고 Tag와 Digest를 기록합니다.
2. App #22의 Backup·Replication·현재 Revision 사전 조건을 확인한 뒤 승인된 One-shot Migration을 실행합니다.
3. Migration Revision, 기존 데이터 보존, Replication 및 MaxScale TLS Read/Write를 확인합니다.
4. GitOps에서 새 Backend Digest와 `replicas: 1`을 반영합니다.
5. 1 Replica의 Image Pull, Pod Ready, DB/Redis readiness, 회원가입·로그인·Room·Game 흐름을 확인합니다.
6. 1 Replica 성공 뒤에만 `replicas: 2`로 올려 공유 Session/Room/Vote, Pub/Sub Event, 재접속, 중복 마감 방지와 Pod 장애 수렴을 확인합니다.
7. Frontend·Gateway를 활성화하고 팀원 PC에서 HTTPS와 WSS를 확인합니다.

각 단계가 실패하면 다음 단계로 진행하지 않습니다. Replica를 0으로 되돌릴 때도 선언적 GitOps 변경과 Argo CD 동기화를 사용하며, 임의의 live patch를 정상 운영 절차로 기록하지 않습니다.

## 관측 항목

- Argo CD Application의 대상 Revision, Sync와 Health
- Deployment의 Image Digest, Replica/Available 수, `imagePullSecrets`
- Pod의 Phase, Ready Condition, Restart 수와 Event
- `/health/startup`, `/health/live`, `/health/ready`의 서로 다른 의미
- Backend 로그의 Provider/Runner 실패 여부(환경변수·URL·Credential 전체 출력 금지)
- Migration Revision과 MariaDB Replication/MaxScale TLS 결과
- Redis 기반 공유 상태와 두 Pod 사이 Event 전달·재접속 결과

팀원 접속 URL, DNS/hosts 및 CA 설치 방식은 Frontend·Gateway Live 검증 뒤 실제 값으로 별도 운영 인계합니다.

## Application Logging 조회·해석

Backend Application 로그는 stdout/stderr에 JSON Line 형식으로 출력한다.

기존 수집 경로는 변경하지 않는다.

    Backend stdout/stderr
    → containerd Pod log
    → Grafana Alloy
    → Loki
    → Grafana

### 주요 필드

기본 Application 로그는 다음 필드를 사용한다.

- `timestamp`: UTC 로그 발생 시각
- `level`: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- `event`: 검색 가능한 Event 식별자
- `logger`: Python logger/module 이름
- `file`: 가능한 경우 `seokpan/...` 기준 Source 경로
- `line`: 로그 호출 Source line
- `function`: 로그 호출 함수
- `instance_id`: Backend Pod별 Instance ID
- `message`: 사람이 읽는 요약
- `request_id`, `room_id`, `game_id`, `turn_no`, `error_code`, `status`: 해당 Context가 있을 때만 포함
- `exception.type`: Exception class
- `exception.frames`: Exception stack의 file / line / function

기존 로그 호출에서 명시적 `event`를 아직 지정하지 않은 경우
`event=application.log`가 사용될 수 있다. 핵심 안정화 경로의 구체적인
event/context는 해당 기능 작업에서 단계적으로 추가한다.

### Quick Reference

현재 프로젝트의 Ansible inventory와 Vault 설정을 사용하기 위해 다음 명령은
`seokpan-infra/ansible` 작업 디렉터리에서 실행한다.

Backend Pod 확인:

    cd ~/work/seokpan-infra/ansible && ansible cp-01 --ask-vault-pass -m shell -a 'export KUBECONFIG=/etc/kubernetes/admin.conf; kubectl -n application get pods -l app.kubernetes.io/name=backend -o wide'

Backend Replica의 최근 로그 확인:

    cd ~/work/seokpan-infra/ansible && ansible cp-01 --ask-vault-pass -m shell -a 'export KUBECONFIG=/etc/kubernetes/admin.conf; kubectl -n application logs -l app.kubernetes.io/name=backend -c backend --tail=100 --prefix=true'

최근 ERROR Application log 빠르게 확인:

    cd ~/work/seokpan-infra/ansible && ansible cp-01 --ask-vault-pass -m shell -a 'export KUBECONFIG=/etc/kubernetes/admin.conf; kubectl -n application logs -l app.kubernetes.io/name=backend -c backend --tail=1000 --prefix=true' | grep -F '"level":"ERROR"'

특정 Event 검색:

    cd ~/work/seokpan-infra/ansible && ansible cp-01 --ask-vault-pass -m shell -a 'export KUBECONFIG=/etc/kubernetes/admin.conf; kubectl -n application logs -l app.kubernetes.io/name=backend -c backend --tail=1000 --prefix=true' | grep -F '"event":"<event-name>"'

특정 Room 검색:

    cd ~/work/seokpan-infra/ansible && ansible cp-01 --ask-vault-pass -m shell -a 'export KUBECONFIG=/etc/kubernetes/admin.conf; kubectl -n application logs -l app.kubernetes.io/name=backend -c backend --tail=1000 --prefix=true' | grep -F '"room_id":"<room-id>"'

### Grafana / Loki에서 조회

Grafana Explore에서 Loki datasource를 선택한다.

Backend 전체 로그:

    {namespace="application", container="backend"}

구조화된 Application JSON 로그만 파싱:

    {namespace="application", container="backend"}
    | json
    | __error__=""

ERROR 로그:

    {namespace="application", container="backend"}
    | json
    | __error__=""
    | level="ERROR"

특정 Event:

    {namespace="application", container="backend"}
    | json
    | __error__=""
    | event="<event-name>"

특정 Room:

    {namespace="application", container="backend"}
    | json
    | __error__=""
    | room_id="<room-id>"

특정 Backend Pod:

    {namespace="application", container="backend", pod="<backend-pod>"}
    | json
    | __error__=""

### 장애 로그 읽는 순서

    timestamp / level
    → event
    → instance_id / pod
    → request_id / room_id / game_id / turn_no
    → file : line / function
    → error_code
    → exception.type / exception.frames

`file`과 `line`은 해당 로그를 출력한 배포 버전을 기준으로 해석한다.
따라서 장애 Evidence에는 가능하면 Backend Image Digest 또는 대응 Source SHA를
함께 기록한다.

Cookie, Session Token, Authorization header, Password, DB/Redis Credential,
Secret 또는 검증되지 않은 사용자 입력 원문은 로그에 기록하지 않는다.

Formatter는 Exception value를 자동으로 log payload에 포함하지 않는다.
다만 호출부에서 Exception 문자열이나 민감정보를 직접 message에 삽입하는 경우까지
자동 차단하는 것은 아니므로, 호출부에서도 같은 민감정보 제외 기준을 적용한다.

### Access Log Noise

다음 반복 health/metrics 요청의 **HTTP status < 400 access log**는 기본 조회 노이즈를 줄이기 위해 억제한다.

- `/health/live`
- `/health/ready`
- `/metrics`

동일 endpoint의 `4xx` / `5xx` access log는 유지한다.
