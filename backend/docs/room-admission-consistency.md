# F05: Session admission race

Canonical: APP #76 / F05. This work is separate from #86 Game lifecycle.

## Status and source boundary

Branch base: `82_ux/board-room-polish-v2@f2014d5ecfb2330d8241a7901731993064b129fb`.
`room/application/lobby.py` has the same blob (`133b3a85f01d3fe6c05a01afebd0e47271e32390`)
at this base and #86's inspected `1f81579d...`. No Game lifecycle changes are needed to reproduce F05.
This checkpoint adds regression tests and records the correction boundary. It does not fix admission,
activate a new path, change a public API, or establish Source Freeze/production readiness.

## Confirmed source path

`create_room()` and `join_room()` perform:

```
replay check -> require_not_participating(session) -> Provider work -> local bind -> notification
```

The participation read may use shared Redis state, but it is separate from the Room write.
Room version checks serialize conflicting writes to the same Room, not to different Rooms.
`RedisRoomRuntimeAdapter._find_binding()` already rejects multiple matches with
`ROOM_PARTICIPATION_AMBIGUOUS`. That is detection after duplicate admission, not prevention.
The HTTP routes authenticate/validate the Session and call these use cases; `RedisSessionWorkflow`
rotation/logout does not enclose create/join as a cross-Room atomic admission operation.

## Deterministic reproducer

`tests/application/test_room_admission_race.py` pauses the first target Provider write after
admission pre-check, schedules the second request, then releases the first writer. It covers:

- join/join to different Rooms, create/join, create/create;
- one service instance and two service instances sharing Room state;
- sequential admission rejection as a control;
- same-Room version conflict as a control.

A shared participation reader is used so the reproducer does not rely on two empty local caches.
The test has a bounded timeout and cleans up tasks. The xfail filter accepts AssertionError only;
timeout and cancellation are not accepted as the known invariant failure.
The six invariant cases are strict xfails for the known unfixed F05, not successful regression fixes.
Running with `--runxfail` exposes the underlying invariant failures. Remove the xfail when the shared
admission implementation is connected. These expected failures must not justify closing F05 or
merging a fix as complete.

## Execution evidence and limitations

The current available environment executed selected unchanged admission/lookup/bind/ID source
methods with DTO/Room Provider/event/constructor boundaries substituted. This is not the entire
service module, the real Memory adapter/Domain test execution, a Redis Lua test, or two actual Pods.
The repository test is written against the real RoomApplicationService and Memory adapter; it still
needs execution with the repository's pinned dependencies.

- Expected-failure override: **6 failed / 4 passed**. Each race produced two memberships/two approvals.
- Normal markers: **4 passed / 6 xfailed**. This does not mean F05 is fixed.
- Syntax and line-length checks were run. Pinned format/ruff/mypy, Redis/MariaDB and Browser were not.

## Next correction boundary — not another lifecycle redesign

Create and join need one shared per-Session admission decision before either Room mutation can win.
The implementation must retain the existing Room version/password/capacity and Session authority
checks. A local asyncio.Lock is not a two-Pod solution; another read after the write is not atomic
prevention. A lease that expires while an accepted write is unresolved must not authorize a second
Room without verifying the first outcome.

Before selecting the smallest Provider change, compare its lifecycle impact on explicit leave,
kick, disconnect expiry, Room closure, identity rotation/logout, replay and ambiguous write outcome.
Do not put a cross-Room Redis key into same-slot scripts without explicitly reviewing their key and
cluster assumptions. Do not introduce a persistent reservation without a defined release/recovery
path. The existing ambiguity detector must not be removed to make the symptom disappear.

Acceptance: competing creates/joins yield one committed participation; loser rejection leaves no
orphan participant/Room; sequential retry and authorized re-entry work; failed/uncertain writes and
identity changes cannot permit a second winner. The six strict xfails must become ordinary passes.

## Integration order

This is a sibling of #86/#88 under #82, not their descendant. Do not add the fix to #86 or grow the
UX stack. When implemented, integrate its unique diff onto a main that includes #82 and review
conflicts with the actual #88 changes. Existing main promotion order remains #82 -> #86 -> #88 ->
#104 -> #85; the admission fix's precise release position is set after its write paths are finalized.
No main merge, deployment, CI configuration or user-PC action was performed for this checkpoint.
