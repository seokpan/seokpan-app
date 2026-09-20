# F05: Session admission race

Canonical: APP #76 / F05. This work is separate from #86 Game lifecycle.

## Status and source boundary

Branch base: `82_ux/board-room-polish-v2@f2014d5ecfb2330d8241a7901731993064b129fb`.
The earlier `12cb1222` checkpoint reproduced the race but did not fix admission.
This change connects guarded Memory/Redis Room adapters through the existing `app.py` and
`production.py` factories. It changes no Game rule, host-disconnect policy, database schema,
CI/CD configuration or live deployment. Pinned repository and actual Provider QA remain pending.

## Confirmed source path

`create_room()` and `join_room()` previously performed:

```
replay check -> require_not_participating(session) -> Provider work -> local bind -> notification
```

Room versions protect one Room, not a Session entering different Rooms. The existing Redis
`_find_binding` rejects multiple matches with `ROOM_PARTICIPATION_AMBIGUOUS`; retain that detector.
The new admission decision does not infer membership from a process-local cache.

## Selected implementation

Use the current single Redis authority and existing Room connection records, not a second durable
membership index. A permanent reservation would need release/recovery changes on every exit path;
a process-local asyncio.Lock would not protect two Backend replicas. A lease check performed only
in Python would allow an expired writer to commit. The selected boundary is:

```
acquire per-Session random-token lease (SET NX PX)
-> re-read committed Room bindings under that lease
-> existing Room Lua, preceded by token + current Session validation
-> release only if the token still belongs to this request
```

The fence and Room mutation are one script execution on the same Redis server. Lease expiry never
means that an old write did not happen. A new holder re-reads existing bindings; a delayed old write
must pass the live token check at mutation time. A release arriving late cannot delete a newer lease.
The 10-second lease is a bounded admission window, not a data-retention or reconnect timeout.
A slow binding scan exceeding it is rejected before mutation, not renewed or blindly replayed.

The token is ARGV[8], outside the original payload, so a fresh token does not alter the request
fingerprint. All accessed keys are declared: the inherited ten Room keys plus lease and Session keys.
The new script prepends its checks to the current `ROOM_MUTATION.source`; it does not copy/freeze
an older Room rule implementation. Its guard runs before cache replay and expiry pruning.

Guarded operations: create, join, identity rebinding and connect (which also writes session_digest).
The script checks the live Session before the Room write; a delayed request using a revoked/expired
Session is rejected. Connect additionally compares the participant's actor type with the Session.
Session-related transient admission errors use the existing SessionRuleViolation handler (503);
SESSION_NOT_FOUND remains 401, and committed membership conflicts remain Room 409 responses.
There is no new UI project-status text, automatic mutation retry or silent legacy fallback.

Memory uses a synchronous check over its existing shared Room connections immediately before the
inherited binding write. It has no second index. The inherited create/join/change_identity methods
must remain non-suspending before binding; connect writes before awaiting its optional Vote mirror.
This is an event-loop reference implementation, not process-restart durability or multi-process Memory.

## Exit, identity and failure boundaries

- Leave, kick, disconnect expiry and Room closure remove actual connections. No additional long-lived
  admission reservation requires cleanup. Temporary disconnect still counts until the real exit.
- Identity replacement and connect use the same new-digest gate as admission; another binding cannot
  be overwritten. Existing Session rotation/logout authority and ambiguity detection are preserved.
- Current Session validation at the final write narrows the revoked-session race. This is not a claim
  that the complete multi-step logout/rotation workflow became a transaction; test those interleavings.
- A committed write with a lost response remains visible to subsequent binding reads. An unexecuted
  delayed write cannot commit after its token was released or replaced.
- Release errors do not mask a committed result or the original exception; expiry permits progress.
  Cancellation is propagated. Acquisition response loss leaves at most the bounded lease.
- Existing Room Lua errors do not roll back partial writes. Corrupt/partial Room state, Redis data loss,
  failover and OOM need actual Provider fault testing/recovery; do not claim this fence repairs them.
  Do not delete ambiguous bindings or infer that every failed response means no write occurred.

## Deployment and integration assumptions

This uses a **single Redis execution authority**. The new Session lease and Room keys are in different
hash slots: Redis Cluster/sharded execution is not supported. Do not use allow-cross-slot flags or
rename existing Room keys to pretend compatibility. Revisit admission ownership before any sharded
migration. Official references: Redis EVAL key declaration, Lua API cluster restrictions, and
Distributed Locks single-instance token/compare-delete guidance.

All active Room writers must be on the guarded version. Drain old HTTP/WS/background/manual writers
before activation; an already-running old script cannot be retroactively fenced. Existing connections
remain the authority and require no speculative index backfill. Existing duplicate/invalid records
must be investigated, not silently selected/deleted. Validate the known standalone deployment first.
No server action, writer drain or data repair was executed during this Source change.

The F05 branch stays a sibling of #86/#88 under #82. Its app.py import change must preserve #86's
lifecycle wiring; its guarded script must wrap the finally integrated #86/#88 Room script. Suggested
release order after those direct checks: #82 -> #86 -> #88 -> F05 -> #104 -> #85. This is an integration
plan, not permission to merge without required tests/CI or to alter the pending host-disconnect policy.

## Execution evidence

Historical reproducer: six competing create/join cases failed, four controls passed; six strict xfails
were explicitly unresolved, not successful fixes. The updated race tests target the guarded Memory
adapter and remove the xfail. Same-Room version rejection and sequential rejection are retained.

Current limited Python harness: **48 PASS** (10 race/control cases plus 38 fence/store/wiring cases).
It executes the exact new adapter code and selected unchanged admission methods, with imports,
DTO/Domain/base Room/Redis writer boundaries substituted. The new factory alias test is AST-based,
not full app import or a live service test. Do not add historical PASS counts to this result.

Exact new lease/fence Lua fragments: **28 scenarios PASS** on Lua5.4 + Redis/cjson doubles. The inherited
Room script body is not executed in that check; this is not real Redis or 2-Pod evidence.
Syntax/100-character line length and baseline/changed Git blob checks were run. The source tree in
the checkpoint is partial; its import/Provider doubles must never be copied into the service tree.

## Remaining acceptance

- Run the real Memory/Domain and entire configured repository tests, format/ruff/mypy.
- Run full inherited Room Lua with the new fence on actual Redis, including cancellation/response loss,
  lease expiry, duplicate binding, old writer exclusion, ACL/OOM and actual Session expiry/rotation.
- Verify create/join/identity/connect competition on two real Backend replicas and authorized re-entry.
- Preserve password, capacity, Room version, cancellation, lifecycle events and API behavior.
- Integrate #86/#88 source and verify no bypass of admission or lifecycle guards.
- Browser/UX acceptance resumes only when the user reports server-PC access.

Source is implemented, but full Source Freeze/Provider PASS/main promotion is not asserted here.
