# MariaDB v1 Baseline·Participant Identity Expand 적용 경계

이 문서는 `stone_game_schema_v1.sql`과 2026-08-30 Runtime `SHOW CREATE TABLE`에서 일치가 확인된 7개 Table의 Alembic 기준을 설명한다. 실제 운영 적용 승인서가 아니며 Password·연결 문자열·Secret 값을 기록하지 않는다.

## 소유권

| 범위 | 소유 |
| --- | --- |
| MariaDB·MaxScale Runtime, `stone_game` Database, 계정·권한 | `seokpan-infra`·DB 담당 |
| SQLAlchemy Model, Table DDL, Alembic Revision | `seokpan-app` |
| 실제 Migration 작업창·Backup·Rollback·적용 | App·DB·GitOps 공동 검토 후 별도 승인 |

Infra가 별도 Table Schema를 두 번째 기준으로 생성하지 않는다. 앱은 실제 Master IP 대신 MaxScale/Common Endpoint가 들어간 연결 문자열을 사용한다.

## 연결 설정

정상 Backend Runtime은 두 개의 최소 권한 연결만 소비한다.

```text
SEOKPAN_IDENTITY_DATABASE_URL  # identity_svc
SEOKPAN_GAME_DATABASE_URL      # game_svc
```

Alembic 단일 실행은 별도 설정을 사용한다.

```text
SEOKPAN_MIGRATION_DATABASE_URL # db_admin, Migration Job/승인 절차 전용
```

정상 Backend Pod에는 Migration Credential을 주입하지 않는다. 실제 값은 Secret 경로로 전달하고 Git·문서·로그에 남기지 않는다.

## 단일 Migration 실행 자산

Repository에서 검증하는 실행 진입점은 `seokpan-migration-gate`다. 이 도구는
`SEOKPAN_MIGRATION_DATABASE_URL`을 소비하지만 Credential이나 전체 URL을 출력하지 않는다.

지원 작업:

```text
current         # 현재 Revision 조회, 읽기 전용
stamp-baseline  # 기존 7개 Table DB를 20260901_0001로 Stamp
upgrade-head    # 승인된 Revision Chain을 head까지 적용
```

모든 작업은 기대 Host·Port·Database와 실제 URL을 먼저 비교한다. 변경 작업은
`--execute`와 별도 Runtime 승인 기록을 가리키는 `--approval-ref`가 모두 없으면 거부한다.
이 검사는 Backup·Replication·Rollback 확인이나 사람의 승인을 대체하지 않는다.
명령은 `alembic.ini`와 `migrations/`가 함께 있는 Backend 작업 디렉터리에서 실행한다.
향후 Container Image도 이 두 실행 자산을 포함해야 한다.

예시의 URL과 승인 참조는 실제 값이 아니다.

```bash
export SEOKPAN_MIGRATION_DATABASE_URL='<db_admin-secret-url>'

uv run seokpan-migration-gate current \
  --expect-host db.seokpan.soldesk.store \
  --expect-database stone_game

uv run seokpan-migration-gate stamp-baseline \
  --expect-host db.seokpan.soldesk.store \
  --expect-database stone_game \
  --approval-ref '<approved-runtime-record>' \
  --execute
```

`db.seokpan.soldesk.store`는 현재 Infra Endpoint Registry의 Canonical DB Endpoint다.
공식 공용 문서 반영과 MaxScale TLS 연결 방식이 확정되기 전에는 위 예시를 실제 실행
승인으로 해석하지 않는다. 이후 Kubernetes 실행 자산은 이 CLI를 정확한 Backend Image
Digest와 Migration 전용 Secret으로 한 번만 호출하며, 정상 Backend Deployment에는
포함하지 않는다.

## 빈 Database

Infra가 빈 `stone_game` Database와 Migration 계정을 준비한 뒤 승인된 단일 실행에서 다음 Revision Chain을 적용한다.

```text
20260901_0001 (stone_game v1 7개 Table)
└─ 20260902_0002 (game_participant.participant_id CHAR(36) NULL)
```

사전 검토 예시:

```text
uv run alembic heads
uv run alembic history
uv run alembic upgrade head --sql
```

Offline SQL 성공은 실제 MariaDB 적용 성공을 뜻하지 않는다. 실제 `upgrade head`는 Backup·대상·작업창·검증·Rollback을 다시 승인한 뒤 한 번만 실행한다.

## 기존 Runtime Database

기존 Runtime에는 이미 동일한 7개 Table과 데이터가 있으므로 초기 Create Revision을 재실행하지 않는다.

1. MaxScale에서 actual Master·Replication 상태를 확인한다.
2. 원본 SQL Checksum과 7개 Table의 `SHOW CREATE TABLE`을 보관·대조한다.
3. Table·Column·PK·FK·UNIQUE·CHECK와 기존 행 상태가 Baseline과 일치하는지 확인한다.
4. Backup·Restore 가능 상태와 Rollback 절차를 확인한다.
5. 승인된 단일 실행에서 `alembic stamp 20260901_0001`을 수행한다.
6. `migrations/audit/game_participant_identity.sql`을 실행하고 결과를 App·DB 담당자가 함께 검토한다.
7. 진행 중 Game이 있으면 Redis의 실제 Participant Mapping을 확보하기 전에는 Backfill을 중단한다.
8. Audit 중단 조건이 해소되고 별도 적용 승인을 받은 뒤 `alembic upgrade 20260902_0002`를 수행한다.
9. `alembic current`, Column, Replication, MaxScale Read/Write와 기존 데이터 보존을 확인한다.

불일치가 있으면 Stamp로 숨기지 않고 중단한다. DDL이나 데이터를 임의 수정하지 않고 차이·영향·정리 Migration을 별도로 검토한다.

## Participant Identity Expand 경계

Revision `20260902_0002`는 기존 행을 보존하기 위해 nullable `participant_id CHAR(36)` Column만 추가한다. Table을 재생성하거나 기존 행을 변경하지 않으며 NOT NULL·UNIQUE·CHECK를 적용하지 않는다.

신규 Application Write는 Application에서 생성한 **소문자 하이픈 UUIDv4**를 `participant_id`로 제공해야 한다. 이 규칙은 후속 Persistence Adapter의 입력 경계이며, 기존 NULL 행을 임의 값으로 채우라는 의미가 아니다.

읽기 전용 Audit SQL은 다음을 확인한다.

- 전체 Participant와 NULL 식별자 수
- 진행 중 Game의 Participant 존재 여부
- Game 안의 Member·Guest Label 중복
- Member/Guest Column 조합 위반
- 이미 저장된 식별자의 UUIDv4 형식과 Game 내 중복

Audit 결과가 하나라도 발견되었다는 사실만으로 자동 수정하지 않는다. 특히 진행 중 Game은 Redis의 권위 Participant Mapping과 대조할 수 없으면 Backfill을 중단한다. 실제 결과에는 Runtime 식별자가 포함될 수 있으므로 원문 전체를 Issue·PR에 복사하지 않고 판정과 필요한 Evidence만 남긴다.

## 이번 단계에 포함하지 않는 변경

- 기존 `game_participant.participant_id`의 UUID Backfill
- `participant_id` NOT NULL·UNIQUE·CHECK 최종 제약
- `game.room_id` 물리 타입 축소·NOT NULL 전환
- Repository와 Result·Stats·Rating Transaction
- Seed·Fixture의 운영 자동 입력

위 항목은 실제 Audit 결과 검토와 별도 Issue·Migration·Provider 검증을 거친다. Offline SQL과 정적 Test 통과는 실제 MariaDB·MaxScale 적용 성공이 아니다.
