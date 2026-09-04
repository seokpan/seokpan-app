# Lobby·Room WebSocket 경계

## 역할

Lobby와 Room WebSocket은 기존 `/api/v1` HTTP 명령이 바꾼 상태를 전달하고, 연결 직후 현재 상태로 수렴시키는 통로입니다. WebSocket으로 상태 변경 명령을 받거나 Redis Key를 직접 다루지 않습니다.

```text
HTTP 명령 성공
→ Room Application 상태 변경
→ Lobby 또는 Room 변경 알림
→ Client가 Version 차이를 확인
→ 필요하면 HTTP Snapshot 재조회
```

현재 구현은 In-memory Event Adapter를 사용하는 Headless 단계입니다. 실제 Redis Pub/Sub, 여러 Backend Replica 사이의 Socket 소유권, Gateway WSS 동작을 검증한 결과가 아닙니다.

## 연결과 첫 Snapshot

- `/ws/v1/lobby`와 `/ws/v1/rooms/{room_id}`는 `seokpan_session` Cookie와 허용된 `Origin`을 검사합니다.
- URL Query의 인증 Token은 받지 않습니다.
- Lobby의 첫 Application 메시지는 `lobby.snapshot`, Room은 `room.snapshot`입니다.
- Room Snapshot에는 현재 Room과, 진행 중인 경우 접속자 기준 Game·Vote Snapshot이 포함됩니다.
- Room에 참가하지 않은 Session은 해당 Room WebSocket에 연결할 수 없습니다.

Envelope의 `state_version`은 HTTP 변경 검사에 쓰는 Resource Version이 아니라 Lobby 또는 Room Stream의 메시지 순서입니다. Lobby와 각 Room이 서로 독립된 Stream Version을 발급하며, 연결 직후 Snapshot은 현재 Stream Version을 사용하되 번호를 새로 증가시키지 않습니다. Room과 Game/Vote의 Resource Version은 Snapshot 객체와 Event Payload의 `room_state_version`·`game_state_version`에 유지합니다.

Lobby는 Room Resource Version을 재사용하지 않고 목록 전용 Stream Version을 사용합니다. Room 생성·종료, 참가자 수, Game 시작·종료에 따른 입장 가능 여부, 목록에 표시되는 설정이 바뀔 때만 증가합니다. 팀과 Ready 변경은 Lobby Version을 증가시키지 않습니다.

## 연결 교체와 단절

Room 연결은 기존 Room Runtime의 `connection_generation`을 사용합니다. 같은 참가자의 새 연결이 열리면 이전 연결에는 `connection.reconnect_required`를 보내고 종료합니다. 이전 Generation의 늦은 종료는 현재 연결 상태를 바꾸지 않습니다.

참가자가 HTTP로 명시적 퇴장하면 해당 참가자의 Room Socket도 `room.participant_left`를 전달한 뒤 닫습니다. Guest→Member 전환처럼 Session 식별값이 바뀐 뒤에도 이미 인증된 Socket의 종료 처리는 연결 시 확인한 Participant ID와 Generation을 사용하므로 새 Session의 참가 상태를 놓치지 않습니다.

일반 Socket 단절은 즉시 다음 처리를 수행합니다.

- 현재 Turn의 마감 전 Vote 제거
- 필요하면 방장 즉시 승계와 모든 Ready 해제
- 참가자는 제거하지 않고 30초 Disconnect Lease 시작

유예 안에 다시 연결하면 참가자·팀·진행 중 Game 상태는 유지하지만 이전 Vote와 방장 권한은 복원하지 않습니다. `room.participant_left`는 명시적 퇴장 또는 유예 만료 뒤에만 전달합니다.

`DisconnectExpiryRunner`는 만료 대상을 다시 읽어 현재 Generation과 Room Version을 확인한 뒤 제거합니다. 동일 대상을 다시 처리하면 상태를 중복 변경하지 않습니다. 실제 Redis 만료 대상 자료구조와 여러 Runner의 경쟁은 Provider 통합 단계에서 검증합니다.

## Event와 실패 처리

공통 Envelope는 `event_type`, `schema_version`, `event_id`, `occurred_at`, `state_version`, 선택적인 `room_id`·`game_id`·`turn_no`, `payload`를 사용합니다. Session 원문, Cookie, 비밀번호 Hash와 다른 참가자의 개인별 Vote는 Event에 포함하지 않습니다.

Game·Vote·Turn에서는 다음 순서를 사용합니다.

```text
game.started
→ vote.tally_changed (실제 집계 변경마다)
→ turn.resolving
→ game.move_applied 또는 turn.passed
→ game.finished (종료된 경우)
→ snapshot.required (Game 종료 뒤 Room WAITING Snapshot 재조회)
```

`game.started`는 Game 저장과 Vote Runtime 초기화가 모두 끝난 뒤에만 전달합니다. `vote.tally_changed`에는 좌표별 집계와 유효 투표자 수만 포함하며 개인별 Vote는 포함하지 않습니다. 공식 Move는 MariaDB 기록 확인과 Runtime 반영 뒤 확정 Board와 함께 전달합니다. 첫 0표 Pass는 다음 Turn 정보를 전달하고, 두 번째 연속 0표는 `turn.resolving`, `turn.passed`, `game.finished` 순으로 공동 패배를 알립니다.

같은 상태 변경을 다시 전달해야 할 때는 기존 `event_id`와 Stream Version을 재사용합니다. Event 전달 실패는 이미 끝난 상태 변경을 되돌리지 않으며, Client는 Version 간격이나 `snapshot.required`를 확인하면 HTTP Snapshot을 다시 조회합니다.

Room이 종료되면 `room.closed`와 Lobby 이동 안내만 전달하며 내부 종료 사유는 Client에 노출하지 않습니다. `WAITING` 종료는 Game 기록을 만들지 않습니다. `PLAYING` 종료에서는 Room Runtime이 후속 처리에 `SYSTEM_INVALID`를 전달하지만, 실제 Game 종료 기록 연결은 Redis·MariaDB Provider 통합 단계에서 검증합니다.

Event 발행 실패는 이미 완료된 HTTP 상태 변경을 되돌리지 않습니다. 실패 로그에는 Event 종류와 Room 식별자만 남기고 Session·Payload는 기록하지 않습니다. Queue가 한도를 넘으면 기존 대기 Event를 버리고 `snapshot.required`를 전달한 뒤 연결을 종료합니다. Client의 Event Version 간격 감지와 Snapshot 재조회는 Frontend 단계에서 연결합니다.

Snapshot 구성 또는 Event 구독 준비가 실패한 연결은 참가자 퇴장이나 패배로 기록하지 않습니다. Backend의 계획된 종료도 참가자의 일반 단절로 기록하지 않습니다. 갑작스러운 Pod 장애의 복구는 실제 Redis·Kubernetes 통합에서 검증합니다.

## 현재 검증과 남은 연동

Headless 테스트는 Cookie·Origin·Query Token 거부, 첫 Snapshot, Room 권한, 연결 교체, 방장 승계·Ready 해제, Vote 제거, 30초 만료, Room 종료 안내, Queue 상한과 Event 실패 뒤 상태 보존을 확인합니다. 또한 Room과 Game/Vote Resource Version 분리, Room Stream 순서, Game 시작·Vote 집계·Turn 마감·Move·Pass·종료 Event의 순서와 공개 Payload를 확인합니다.

다음 항목은 후속 작업입니다.

- `PLAYING` Room 종료의 `SYSTEM_INVALID` Game 기록 연결
- Redis Pub/Sub과 만료 대상 조회
- 여러 Backend Replica의 Socket·Runner 경쟁
- 갑작스러운 Pod 장애와 재접속 복구
- Frontend Version 간격 감지와 Snapshot 재조회
- Gateway HTTPS/WSS와 Kubernetes Runtime 검증
