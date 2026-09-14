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
