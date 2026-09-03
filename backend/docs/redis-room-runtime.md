# Redis Room Runtime Adapter 경계

## 책임

Room Runtime의 권위 저장소는 Redis다. Domain은 방·참가자·팀·Ready·방장 승계와 종료 규칙을 판단하고, Adapter는 동일 규칙을 Redis Lua에서 한 번에 반영한다. HTTP와 WebSocket은 Redis Client, Key 문자열 또는 Lua를 직접 사용하지 않는다.

이번 구현은 같은 Room의 모든 Key에 Redis Cluster Hash Tag `{room_id}`를 사용한다.

```text
stone:v1:room:{room_id}:meta
stone:v1:room:{room_id}:participants
stone:v1:room:{room_id}:ready
stone:v1:room:{room_id}:connections
stone:v1:room:{room_id}:requests
stone:v1:room:{room_id}:request-expiries
stone:v1:room:{room_id}:closed
stone:v1:room:{room_id}:votes:{turn_no}
```

Room 전체를 하나의 JSON 값으로 저장하지 않는다. Meta와 참가자, Ready, Connection, 중복 요청을 분리하고 한 Room의 변경만 Lua에서 원자 처리한다. 상태 변경 명령은 `expected_state_version`을 검사하고 stale 명령을 `STATE_VERSION_CONFLICT`로 거부한다. 활성 Room Key에는 서로 다른 짧은 TTL을 두지 않는다.

## 공개·비공개 Room

공개 Snapshot에는 `visibility`와 `password_required`만 나타난다. 비공개 Room의 Redis Meta에는 Argon2id Encoded Hash만 저장하며 비밀번호 원문은 Runtime Adapter에 전달하지 않는다. Hash 생성·검증은 `RoomPasswordPort`의 책임이고, 검증 성공 여부만 입장 명령에 전달한다.

Snapshot에는 Encoded Hash와 Session Digest, Connection Generation을 포함하지 않는다. 새 연결에 발급된 Generation은 해당 명령 결과로만 반환한다.

## Connection과 방장 승계

- `state_version`은 Client가 수렴하는 공개 Room Snapshot의 Version이다. Session Digest, Connection Generation처럼 Snapshot에 노출되지 않는 연결 fencing 값만 교체될 때는 증가하지 않는다.
- 이미 `connected`인 참가자가 새 Generation으로 연결을 대체하면 공개 Snapshot은 그대로이므로 `state_version`을 유지한다. 단절 상태의 참가자가 재접속해 공개 `connected` 값이 바뀌면 한 번 증가한다.
- Session·Room별 새 연결은 Generation을 증가시키고 이전 연결을 대체한다.
- 이전 Generation에서 늦게 도착한 Disconnect는 Room 상태를 변경하지 않는다.
- 일반 참가자의 Disconnect Lease는 Redis `TIME` 기준 30초다.
- 방장 Disconnect는 30초를 기다리지 않고 접속 중인 Member 중 입장 순서가 가장 빠른 참가자에게 즉시 승계한다.
- 방장 승계, 모든 Ready 해제와 `state_version` 1회 증가는 같은 Lua 실행에 포함한다.
- 이전 방장이 재접속해도 방장으로 자동 복귀하지 않는다.
- 승계할 Member가 없으면 Room Runtime Key를 제거하고 10분 Tombstone을 남긴다.
- WAITING 종료는 Game 기록을 만들지 않고, PLAYING 종료만 후속 흐름에 `SYSTEM_INVALID`를 전달한다.
- Room 종료에는 뒤따를 공개 Snapshot이 없으므로 삭제 직전 `state_version`을 따로 증가시키지 않는다. Key 삭제·Tombstone 생성·`room_closed` 종료 결과를 한 원자 처리로 반환하며, 후속 HTTP/WebSocket 계층은 이 종료 결과로 Room 종료와 Lobby 이동을 알린다.

단절·퇴장 명령은 현재 Turn 번호가 주어진 경우 해당 참가자의 Vote를 같은 Hash Slot의 Vote Key에서 함께 제거한다. Vote 생성·교체·마감과 Resolver Lease는 후속 A-05c가 이 경계를 확장한다.

## 멱등성과 오류

변경 명령은 Room별 `request_id` 결과를 24시간 보존한다. 같은 요청을 다시 실행하면 상태를 다시 변경하지 않고 최초 결과를 `replayed=true`로 반환하며, 같은 ID에 다른 명령을 넣으면 `REQUEST_ID_CONFLICT`로 거부한다. 만료 시각은 별도 ZSET으로 관리해 명령 실행 시 만료 결과를 정리한다.

Provider 오류는 URL·Credential·Key·Password·Token을 포함하지 않는 안정적인 코드로 변환한다. 종료 Tombstone이 남아 있는 동안 같은 Room ID의 즉시 재사용을 거부한다.

## 검증 경계

In-memory Fake와 Scripted Redis Client가 같은 Contract Test를 통과한다. 이 검증은 Room 의미, Adapter 입출력, Key Hash Slot, Lua Script Cache와 오류 경계를 확인하지만 실제 Redis Provider의 원자성·경합·AOF/PVC 복구를 증명하지 않는다.

실제 Provider 검증은 Redis `8.10.1`과 `redis.platform.svc.cluster.local:6379`을 대상으로 별도 승인된 P3 단계에서 수행한다. `seokpan-gitops#7`의 Backend Pod DNS 연결과 `seokpan-infra#115`의 복구 검증 결과를 함께 확인한다.
