# Game MariaDB Persistence 경계

이 문서는 Game Pure Domain의 확정 결과를 기존 `stone_game` 7개 Table에 저장하는
Application Port와 MariaDB Adapter의 책임을 설명한다. 실제 Runtime DB 적용 승인서나
연결 검증 결과가 아니다.

## 책임 분리

- `seokpan.game.application`은 Provider 중립 Command와 `GamePersistencePort`를 소유한다.
- `seokpan.persistence.mariadb`는 SQLAlchemy AsyncSession, Table Mapping과 Transaction을
  소유한다.
- Domain은 SQLAlchemy·MariaDB Driver·환경변수를 직접 참조하지 않는다.
- Adapter는 정상 Backend의 `SEOKPAN_GAME_DATABASE_URL` 경계에서 조립하며
  `SEOKPAN_MIGRATION_DATABASE_URL`을 소비하지 않는다.

## 저장 단위

| Command | 원자적 저장 범위 |
| --- | --- |
| Game 시작 | `game`과 시작 시점 PLAYER `game_participant` Snapshot |
| 공식 Move | `move` 한 행 |
| Game 종료 | `game`, `game_result`, 대상 `member_stats`, `member.rating`, `rating_history` |

신규 `game_id`, `room_id`, `participant_id`는 Application 경계에서 소문자 하이픈 UUIDv4로
검증한다. Domain 좌표 `A-O / 1-15`는 기존 Schema의 `pos_x / pos_y = 0-14`로 변환한다.
Pass는 공식 Move가 아니므로 `move` 행을 만들지 않는다.

## 멱등성과 동시성

- Game·Move·Result가 이미 있으면 같은 결정인지 전체 저장 값으로 비교한다.
- 같은 결정은 `UNCHANGED`로 수렴하고 다른 결정은 안정적인 Conflict 오류로 거부한다.
- Result Transaction은 Game을 먼저 잠그고 Member ID 오름차순으로 Member를 잠근다.
- 기존 Result의 `reflected_to_stats=true`와 Game별 Rating History가 예상 결과와 모두
  일치할 때만 완료된 재시도로 인정한다.
- Commit 결과를 확인할 수 없으면 새 Session에서 같은 행을 재조회한다. 완료 상태가
  확인되지 않으면 `PERSISTENCE_COMMIT_UNCERTAIN`으로 중단하며 자동 재반영하지 않는다.
- `SYSTEM_INVALID`도 종료 처리가 끝났다는 Marker는 남기되 Member 전적·Rating과
  Rating History는 변경하지 않는다.

## Provider Gate

현재 Test는 Fake/Scripted AsyncSession과 MySQL용 SQLAlchemy Model을 사용한다. SQLite
성공을 MariaDB 호환 증거로 사용하지 않는다. 다음 항목이 확인되기 전에는 실제 DB 연결,
Migration, Stamp, Audit, DDL 또는 데이터 변경을 수행하지 않는다.

1. `seokpan-infra#102` MaxScale Listener TLS와 Backend Client 연결 방식 완료
2. `db.seokpan.soldesk.store:3306` Endpoint와 `game_svc` Secret 참조 확정
3. 적용 직전 Backup·Replication·Rollback 상태 확인
4. App·DB·GitOps 담당이 확인한 별도 Runtime 작업 승인

Provider Gate가 열리면 같은 Port 시나리오를 실제 MariaDB/MaxScale에서 다시 검증하며,
Fake Test 통과와 실제 Provider 통합 성공을 별도로 기록한다.
