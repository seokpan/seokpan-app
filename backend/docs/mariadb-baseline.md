# MariaDB v1 Baseline 적용 경계

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

## 빈 Database

Infra가 빈 `stone_game` Database와 Migration 계정을 준비한 뒤 승인된 단일 실행에서 다음 Revision Chain을 적용한다.

```text
20260901_0001 (stone_game v1 7개 Table)
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
6. `alembic current`, Replication, MaxScale Read/Write와 기존 데이터 보존을 확인한다.

불일치가 있으면 Stamp로 숨기지 않고 중단한다. DDL이나 데이터를 임의 수정하지 않고 차이·영향·정리 Migration을 별도로 검토한다.

## 이번 Baseline에 포함하지 않는 변경

- `game_participant.participant_id` 추가·Backfill·제약
- `game.room_id` 물리 타입 축소·NOT NULL 전환
- Repository와 Result·Stats·Rating Transaction
- Seed·Fixture의 운영 자동 입력

위 항목은 기존 행 Audit과 별도 Issue·Migration·실제 Provider 검증을 거친다.
