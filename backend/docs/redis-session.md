# Redis Session Adapter 경계

## 책임

Redis는 HTTP와 WebSocket이 함께 사용하는 서버측 Session의 권위 저장소다. Application은 원문 Cookie Token을 외부에 전달한 뒤 SHA-256 Digest만 Adapter에 넘긴다. Redis Key·값·오류·Log에는 원문 Session Token을 저장하지 않는다.

이번 구현은 다음 Key Family를 사용한다.

```text
stone:v1:session:<session_digest>
stone:v1:identity:member:<member_id>:sessions
```

Session 자료는 `schema_version=1`인 UTF-8 JSON이다. Member Session Index는 Digest를 Member별 ZSET에 저장하고 각 Session의 현재 Idle 만료 시각을 Score로 사용한다. 생성·갱신·회전·폐기 때 만료 항목을 정리하고 가장 늦은 Score에 맞춰 Index Key도 만료한다.

## Lifecycle

- Idle TTL: 2시간
- Absolute TTL: 24시간
- 생성·갱신·회전 시각: Redis `TIME`
- Touch: Idle 만료만 갱신하며 최초 Absolute 만료는 바꾸지 않음
- Rotate: 이전 Session 삭제, 이전 Member Index 정리, 새 Session 생성과 새 Member Index 등록을 하나의 Lua 실행으로 처리
- Revoke: 이미 없어진 Session을 다시 폐기하면 변경 없이 수렴

Guest 발급, Member 로그인과 권한 상승은 새 opaque Token과 CSRF Token을 만든 뒤 Digest를 `CreateSession`으로 전달한다. Token 생성·Cookie 응답·Origin/CSRF HTTP 검사는 후속 Headless API 작업의 책임이다.

## Script와 오류

각 Lua Source는 이름·Version·SHA-1 Redis Script Cache 식별값을 가진다. Adapter는 먼저 `EVALSHA`를 사용하고 `NOSCRIPT`일 때 정확한 Source를 `SCRIPT LOAD`한 뒤 한 번 다시 실행한다. 이 SHA-1은 보안 Token Digest가 아니라 Redis가 요구하는 Script 식별 방식이다.

Provider 오류는 URL·Credential·Key·Token을 포함하지 않는 `REDIS_PROVIDER_UNAVAILABLE` 등 안정적인 코드로 변환한다. 손상되거나 지원하지 않는 자료는 인증 성공으로 자동 보정하지 않는다.

## 검증 경계

In-memory Fake와 Scripted Redis Client를 이용한 Contract Test는 Session 의미와 Adapter 입출력, Script cache miss 경계를 검증한다. 실제 Redis `8.10.1`의 TTL·Lua 원자성·두 Backend 경합·AOF/PVC 복구·Backend Pod DNS 연결 성공을 뜻하지 않는다.

실제 Provider 검증은 `seokpan-gitops#7`의 `redis.platform.svc.cluster.local:6379` Runtime을 대상으로 별도 승인된 P3 단계에서 수행한다.
