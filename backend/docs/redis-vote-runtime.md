# Redis Vote·Turn·Resolver Runtime Adapter 경계

## 책임 분리

- Pure Domain은 좌표, 점유, 흑 금수, 득표 결과, Pass, 공동 패배와 Move 적용 결과를 판단한다.
- MariaDB는 공식 Move·Result·Rating의 권위 저장소이며 Unique 제약으로 중복 Write를 최종 차단한다.
- Redis는 현재 Game·Turn·Board·Vote·집계와 Resolver Lease의 권위 Runtime State를 원자 갱신한다.
- HTTP·WebSocket은 Redis Client, Key 문자열이나 Lua를 직접 사용하지 않는다.

Redis Lua가 Renju 규칙을 다시 구현하지 않는다. 좌표는 동일 `game_id`·`turn_no`·`state_version`에서 Domain 검증을 통과한 뒤 Vote로 반영한다. Move 확정 시에는 Domain이 계산하고 MariaDB 결과 확인이 끝난 `TurnResolution`만 Redis에 전달한다. `persistence_confirmed=false`인 Resolution은 입력 경계에서 거부한다.

## Key 구조

```text
stone:v1:room:{room_id}:game
stone:v1:room:{room_id}:board
stone:v1:room:{room_id}:votes:{turn_no}
stone:v1:room:{room_id}:vote-tally:{turn_no}
stone:v1:room:{room_id}:resolver:{turn_no}
```

Room Meta·Participant·Connection·Request Key는 [Redis Room Runtime Adapter 경계](redis-room-runtime.md)의 Key를 재사용한다. 같은 Room의 Key는 모두 `{room_id}` Hash Tag를 사용한다. Game Meta, Board, Vote, Tally와 Resolver를 역할별로 나누며 Room 전체를 하나의 JSON 값으로 저장하지 않는다.

## Vote와 마감

- 현재 팀의 연결된 PLAYER만 `ACTIVE / VOTING` 상태와 Redis 서버 deadline 이전에 Vote를 등록·교체·삭제한다.
- `request_id`는 동일 명령 결과를 24시간 재사용하고 다른 명령의 ID 재사용은 `REQUEST_ID_CONFLICT`로 거부한다.
- `expected_state_version`이 다르면 상태를 바꾸지 않고 `STATE_VERSION_CONFLICT`로 거부한다.
- Game/Vote `state_version`은 `room:{room_id}:game`에 보관하며 Room Meta의 `state_version`과 서로 독립적으로 증가한다.
- 단절·퇴장은 Room Lua가 같은 Vote·Tally Key에서 마감 전 표와 집계를 함께 제거한다.
- 단절·퇴장으로 PLAYER 연결 상태나 Vote가 바뀌면 Room Lua가 Game/Vote Version도 같은 실행에서 한 번 증가시킨다.
- 이 연동의 Room Mutation Script는 v6, Vote Mutation·Read Script는 v3이며 관련 Key를 같은 Room Hash Slot에서 갱신한다.
- 마감은 Redis 서버 시각을 기준으로 Vote를 고정하고 한 번만 `RESOLVING` 또는 Pass로 전이한다.
- 첫 0표 Pass는 `turn_no`와 연속 Pass 횟수만 진행하고 `move_no`를 유지한다. 두 번째 연속 0표는 `JOINT_LOSS` 후보로 `RESOLVING`에 머물며, 공식 Result 저장이 확인된 뒤에만 Redis 종료 상태로 반영한다.
- 두 번째 0표의 대기 상태와 마감 시점 유효 투표자 수를 보존하는 Vote Runtime Schema·Script는 v2다.
- 공식 Move가 확정되면 연속 Pass 횟수를 0으로 초기화한다.

## Resolver와 장애 수렴

- 마감 결과에 후보가 있으면 `resolution_id`로 5초 Resolver Lease를 획득한다.
- Lease 보유자는 제한적으로 갱신할 수 있고, 만료 뒤 다른 Backend가 새 `resolution_id`로 인계받을 수 있다.
- 동률 선택은 Redis Adapter 내부 난수가 아니라 서버 Application 계층이 후보 중 하나를 선택해 전달하며, 선택 결과를 확정 자료에 포함해 재현 가능하게 남긴다. Browser가 선택 결과를 확정하지 않는다.
- Lease를 잃었거나 MariaDB Commit 성공 여부가 불명확하면 Redis 확정을 반복하지 않고 기존 MariaDB Move·Result를 먼저 조회한다.
- MariaDB 확정 후 Redis 갱신에 실패하면 같은 확정 결과를 이용해 Redis Board·Turn·Snapshot을 멱등하게 재동기화한다.
- 종료 결과가 확인되면 Room은 `PLAYING`에서 `WAITING`으로 돌아가고 `game_id`와 모든 Ready가 함께 정리된다.
- Pub/Sub은 식별자와 새 Version을 알리는 무효화 신호일 뿐 권위 상태나 Replay 원본이 아니다.

## 현재 검증 경계

In-memory Fake와 Scripted Redis Client가 같은 Contract Test를 통과하도록 구성한다. Scripted Client는 Port·Key·Codec·Lua 호출 경계와 Adapter 입출력을 검증하지만 Lua Source 자체를 실행하지 않는다. 따라서 이 결과는 실제 Redis 상태 전이의 실행 증거가 아니다.

다음 항목은 별도 Provider Integration Gate에서 확인한다.

- Redis 8.10.1의 Lua·TIME·EVALSHA·SCRIPT LOAD
- 두 Backend Replica의 동시 Vote·마감 경쟁과 Resolver Lease 인계
- Backend Pod에서 `redis.platform.svc.cluster.local:6379` 연결
- AOF/PVC와 Pod 재기동 뒤 Runtime State 수렴
- MariaDB·MaxScale를 통한 공식 Move·Result 저장과 Redis 재동기화

Redis Sentinel/Cluster는 MVP 범위가 아니다. `{room_id}` Hash Tag는 후속 확장을 막지 않기 위한 Key 경계이며 Cluster 검증 완료를 의미하지 않는다.
