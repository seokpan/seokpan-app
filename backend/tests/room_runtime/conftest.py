from __future__ import annotations

from dataclasses import dataclass

import pytest
from redis.exceptions import NoScriptError

from seokpan.persistence.memory import InMemoryRoomRuntimeAdapter, ManualClock
from seokpan.persistence.redis.common import VersionedJsonCodec
from seokpan.persistence.redis.room_adapter import RedisRoomRuntimeAdapter
from seokpan.persistence.redis.room_scripts import (
    ROOM_MUTATION,
    ROOM_PRIVATE_HASH_READ,
    ROOM_READ,
)
from seokpan.room.application import (
    ChangeRoomTeam,
    ChangeRoomVoteSeconds,
    ConnectRoomParticipant,
    CreateRoomRuntime,
    DisconnectRoomParticipant,
    ExpireRoomDisconnect,
    JoinRoomRuntime,
    LeaveRoomRuntime,
    RoomMutationResult,
    RoomRuntimePort,
    RoomRuntimeSnapshot,
    SetRoomReady,
)
from seokpan.room.domain import ActorType, RoomConfig, RoomRuleViolation, RoomVisibility, Team


@dataclass(slots=True)
class RoomRuntimeHarness:
    adapter: RoomRuntimePort
    clock: ManualClock
    observable: InMemoryRoomRuntimeAdapter


class EmulatedRoomRedisClient:
    """Lua command-boundary emulator; it is not actual Redis evidence."""

    def __init__(self, clock: ManualClock, *, scripts_loaded: bool = True) -> None:
        self.store = InMemoryRoomRuntimeAdapter(clock)
        self.loaded = (
            {ROOM_MUTATION.sha, ROOM_READ.sha, ROOM_PRIVATE_HASH_READ.sha}
            if scripts_loaded
            else set()
        )
        self.evalsha_calls: list[tuple[str, int, tuple[object, ...]]] = []
        self.script_load_calls: list[str] = []

    async def get(self, key: str) -> None:
        return None

    async def evalsha(
        self,
        sha: str,
        numkeys: int,
        *keys_and_args: object,
    ) -> bytes:
        self.evalsha_calls.append((sha, numkeys, keys_and_args))
        if sha not in self.loaded:
            raise NoScriptError("script cache miss")
        keys = tuple(str(item) for item in keys_and_args[:numkeys])
        args = keys_and_args[numkeys:]
        room_id = self._room_id(keys[0])
        if sha == ROOM_READ.sha:
            return self._response(snapshot=await self.store.get(room_id))
        if sha == ROOM_PRIVATE_HASH_READ.sha:
            return self._encode(
                {
                    "ok": True,
                    "encoded_password": await self.store.get_private_access_hash(room_id),
                    "error": None,
                }
            )
        if sha != ROOM_MUTATION.sha:
            raise AssertionError("unknown script")

        operation = str(args[1])
        request_id = str(args[2])
        payload = VersionedJsonCodec.decode(str(args[6]))
        try:
            result = await self._mutate(room_id, request_id, operation, payload)
        except RoomRuleViolation as error:
            return self._encode({"ok": False, "error": error.code})
        return self._response(result=result)

    async def script_load(self, script: str) -> str:
        self.script_load_calls.append(script)
        for candidate in (ROOM_MUTATION, ROOM_READ, ROOM_PRIVATE_HASH_READ):
            if candidate.source == script:
                self.loaded.add(candidate.sha)
                return candidate.sha
        raise AssertionError("unknown script source")

    async def _mutate(
        self,
        room_id: str,
        request_id: str,
        operation: str,
        payload: dict[str, object],
    ) -> RoomMutationResult:
        if operation == "create":
            return await self.store.create(
                CreateRoomRuntime(
                    room_id=room_id,
                    request_id=request_id,
                    config=RoomConfig(
                        name=str(payload["name"]),
                        visibility=RoomVisibility(str(payload["visibility"])),
                        max_participants=int(str(payload["max_participants"])),
                        minimum_ready=int(str(payload["minimum_ready"])),
                        vote_seconds=int(str(payload["vote_seconds"])),
                    ),
                    owner_id=str(payload["owner_id"]),
                    owner_session_digest=str(payload["session_digest"]),
                    encoded_password=str(payload["encoded_password"]) or None,
                )
            )
        if operation == "join":
            return await self.store.join(
                JoinRoomRuntime(
                    room_id=room_id,
                    request_id=request_id,
                    participant_id=str(payload["participant_id"]),
                    actor_type=ActorType(str(payload["actor_type"])),
                    session_digest=str(payload["session_digest"]),
                    expected_state_version=int(str(payload["expected_state_version"])),
                    private_access_verified=bool(payload["private_access_verified"]),
                )
            )
        if operation == "change_team":
            return await self.store.change_team(
                ChangeRoomTeam(
                    room_id,
                    request_id,
                    str(payload["participant_id"]),
                    Team(str(payload["team"])),
                    int(str(payload["expected_state_version"])),
                )
            )
        if operation == "set_ready":
            return await self.store.set_ready(
                SetRoomReady(
                    room_id,
                    request_id,
                    str(payload["participant_id"]),
                    bool(payload["ready"]),
                    int(str(payload["expected_state_version"])),
                )
            )
        if operation == "change_vote_seconds":
            return await self.store.change_vote_seconds(
                ChangeRoomVoteSeconds(
                    room_id,
                    request_id,
                    str(payload["actor_id"]),
                    int(str(payload["vote_seconds"])),
                    int(str(payload["expected_state_version"])),
                )
            )
        if operation == "connect":
            return await self.store.connect(
                ConnectRoomParticipant(
                    room_id,
                    request_id,
                    str(payload["participant_id"]),
                    str(payload["session_digest"]),
                    int(str(payload["expected_state_version"])),
                )
            )
        active_vote_turn = payload.get("active_vote_turn")
        normalized_turn = None if active_vote_turn is None else int(str(active_vote_turn))
        if operation == "disconnect":
            return await self.store.disconnect(
                DisconnectRoomParticipant(
                    room_id,
                    request_id,
                    str(payload["participant_id"]),
                    int(str(payload["connection_generation"])),
                    int(str(payload["expected_state_version"])),
                    normalized_turn,
                )
            )
        if operation == "expire_disconnect":
            return await self.store.expire_disconnect(
                ExpireRoomDisconnect(
                    room_id,
                    request_id,
                    str(payload["participant_id"]),
                    int(str(payload["connection_generation"])),
                    int(str(payload["expected_state_version"])),
                    normalized_turn,
                )
            )
        if operation == "leave":
            return await self.store.leave(
                LeaveRoomRuntime(
                    room_id,
                    request_id,
                    str(payload["participant_id"]),
                    int(str(payload["expected_state_version"])),
                    normalized_turn,
                )
            )
        raise AssertionError("unknown operation")

    @classmethod
    def _response(
        cls,
        *,
        result: RoomMutationResult | None = None,
        snapshot: RoomRuntimeSnapshot | None = None,
    ) -> bytes:
        value: dict[str, object] = {
            "ok": True,
            "error": None,
            "snapshot": cls._snapshot(snapshot if result is None else result.snapshot),
        }
        if result is not None:
            value.update(
                {
                    "replayed": result.replayed,
                    "connection_generation": result.connection_generation,
                    "disconnect_expires_at_ms": result.disconnect_expires_at_ms,
                    "stale_connection": result.stale_connection,
                    "vote_removed": result.vote_removed,
                    "departure": (
                        None
                        if result.departure is None
                        else {
                            "previous_owner_id": result.departure.previous_owner_id,
                            "new_owner_id": result.departure.new_owner_id,
                            "room_closed": result.departure.room_closed,
                            "game_termination": result.departure.game_termination.value,
                        }
                    ),
                }
            )
        return cls._encode(value)

    @staticmethod
    def _snapshot(snapshot: RoomRuntimeSnapshot | None) -> dict[str, object] | None:
        if snapshot is None:
            return None
        return {
            "schema_version": snapshot.schema_version,
            "room_id": snapshot.room_id,
            "config": {
                "name": snapshot.config.name,
                "visibility": snapshot.config.visibility.value,
                "max_participants": snapshot.config.max_participants,
                "minimum_ready": snapshot.config.minimum_ready,
                "vote_seconds": snapshot.config.vote_seconds,
            },
            "status": snapshot.status.value,
            "owner_id": snapshot.owner_id,
            "state_version": snapshot.state_version,
            "participants": [
                {
                    "participant_id": item.participant_id,
                    "actor_type": item.actor_type.value,
                    "joined_order": item.joined_order,
                    "connected": item.connected,
                    "team": item.team.value,
                    "ready": item.ready,
                }
                for item in snapshot.participants
            ],
        }

    @staticmethod
    def _encode(value: dict[str, object]) -> bytes:
        return VersionedJsonCodec.encode(value).encode()

    @staticmethod
    def _room_id(key: str) -> str:
        return key.split("{", maxsplit=1)[1].split("}", maxsplit=1)[0]


@pytest.fixture(params=("memory", "redis-boundary"))
def room_harness(request: pytest.FixtureRequest) -> RoomRuntimeHarness:
    clock = ManualClock(now_ms=1_000)
    observable = InMemoryRoomRuntimeAdapter(clock)
    if request.param == "memory":
        return RoomRuntimeHarness(adapter=observable, clock=clock, observable=observable)
    client = EmulatedRoomRedisClient(clock)
    return RoomRuntimeHarness(
        adapter=RedisRoomRuntimeAdapter(client),
        clock=clock,
        observable=client.store,
    )


def digest(character: str) -> str:
    return character * 64


def create_room(
    *,
    request_id: str = "create-1",
    visibility: RoomVisibility = RoomVisibility.PUBLIC,
) -> CreateRoomRuntime:
    return CreateRoomRuntime(
        room_id="room-1",
        request_id=request_id,
        config=RoomConfig(name="MVP Room", visibility=visibility),
        owner_id="member-1",
        owner_session_digest=digest("a"),
        encoded_password=(
            "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"
            if visibility is RoomVisibility.PRIVATE
            else None
        ),
    )


def join_member(
    participant_id: str,
    *,
    request_id: str,
    session_character: str,
    expected_state_version: int = 1,
    private_access_verified: bool = False,
) -> JoinRoomRuntime:
    return JoinRoomRuntime(
        room_id="room-1",
        request_id=request_id,
        participant_id=participant_id,
        actor_type=ActorType.MEMBER,
        session_digest=digest(session_character),
        expected_state_version=expected_state_version,
        private_access_verified=private_access_verified,
    )


def join_guest(
    participant_id: str,
    *,
    request_id: str,
    expected_state_version: int = 1,
) -> JoinRoomRuntime:
    return JoinRoomRuntime(
        room_id="room-1",
        request_id=request_id,
        participant_id=participant_id,
        actor_type=ActorType.GUEST,
        session_digest=digest("f"),
        expected_state_version=expected_state_version,
    )
