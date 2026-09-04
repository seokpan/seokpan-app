# Lobby·Room HTTP 경계

## 범위

Issue #39는 인증·Session HTTP와 기존 Room Domain·Runtime Adapter를 연결한다. Browser나
WebSocket 없이 다음 흐름을 확인할 수 있다.

```text
Member 로그인 → Room 생성 → Guest·Member 입장 → 팀·Ready·설정 변경 → 명시적 퇴장
```

HTTP 명령은 `/api/v1/rooms` 아래의 목록, 생성, Snapshot, 입장, 퇴장, 설정, 팀, Ready
Endpoint로 제공한다. Game 시작·Vote·Turn과 WebSocket Event는 후속 작업 범위다.

## 참가 식별과 Session 전환

Room ID와 Participant ID는 서버가 UUIDv4로 발급한다. Participant ID는 로그인 계정이나
Session Token이 아니며 Room 안에서 유지되는 식별자다.

- 한 Session은 동시에 하나의 Room 참가 상태만 가진다.
- Guest가 Member로 로그인하면 Session은 회전하지만 Participant ID·Room·팀·Ready는
  유지한다.
- Room 참가 중인 Member가 다른 Member 계정으로 바꾸는 요청은 거부한다.
- 로그아웃은 Room 퇴장을 먼저 완료한 뒤 Session을 폐기한다. Room 변경이 실패하면 기존
  Session과 참가 상태를 유지한다.
- `GET /api/v1/session`은 현재 `room_id`와 `participant_id`를 함께 반환하며 미참가 상태에서는
  두 값을 `null`로 반환한다.

## 상태 변경과 공개 응답

생성·입장은 UUIDv4 `request_id`로 같은 요청의 결과를 재사용한다. 설정·팀·Ready·퇴장은
`expected_state_version`을 검사하며, 오래된 요청은 `409 STALE_STATE`와 현재 Version,
Snapshot 경로를 반환한다. 상태 변경에는 Session Cookie와 허용 Origin·Referer,
`X-CSRF-Token` 검사를 적용한다.

비공개 Room 비밀번호는 HTTP 입력에서 Argon2id Provider 경계를 거쳐 Encoded Hash로만 Runtime에
전달한다. Lobby와 Snapshot에는 `password_required`만 공개하고 Hash, Session Digest,
CSRF·Cookie 원문, Connection 내부값은 포함하지 않는다.

## 현재 검증 경계

Application Factory의 Headless 구성은 In-memory Room Runtime과 참가 인덱스를 사용한다. 이
구성은 HTTP·Domain 흐름을 검증하기 위한 Fake이며 실제 Redis의 Room 목록 인덱스, Session과
참가 상태의 동시 변경, 여러 Backend Replica 간 경합 처리를 완료했다는 뜻이 아니다. 해당
항목은 Redis Provider 통합 작업에서 Lua·Key·TTL과 함께 검증한다.
