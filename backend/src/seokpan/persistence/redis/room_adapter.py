"""redis.asyncio-backed Room runtime adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from seokpan.persistence.redis.common import (
    LuaScriptRunner,
    RedisClient,
    RedisKeyspace,
    RedisProviderError,
    VersionedJsonCodec,
)
from seokpan.persistence.redis.room_scripts import (
    ROOM_MUTATION,
    ROOM_PRIVATE_HASH_READ,
    ROOM_READ,
)
from seokpan.room.application.runtime import (
    ROOM_CLOSED_TOMBSTONE_TTL_MS,
    ROOM_DISCONNECT_LEASE_MS,
    ROOM_REQUEST_DEDUPE_TTL_MS,
    ROOM_RUNTIME_SCHEMA_VERSION,
    ChangeRoomIdentity,
    ChangeRoomTeam,
    ChangeRoomVoteSeconds,
    ConnectRoomParticipant,
    CreateRoomRuntime,
    DisconnectRoomParticipant,
    ExpireRoomDisconnect,
    JoinRoomRuntime,
    LeaveRoomRuntime,
    RoomMutationResult,
    RoomRuntimeParticipant,
    RoomRuntimeSnapshot,
    SetRoomReady,
    validate_room_id,
)
from seokpan.room.domain import (
    ActorType,
    DepartureResult,
    GameTermination,
    RoomConfig,
    RoomRuleViolation,
    RoomStatus,
    RoomVisibility,
    Team,
)


class RedisRoomRuntimeAdapter:
    def __init__(self, client: RedisClient) -> None:
        self._scripts = LuaScriptRunner(client)

    async def create(self, command: CreateRoomRuntime) -> RoomMutationResult:
        return await self._mutate(
            command.room_id,
            command.request_id,
            "create",
            {
                "schema_version": ROOM_RUNTIME_SCHEMA_VERSION,
                "name": command.config.name,
                "visibility": command.config.visibility.value,
                "encoded_password": command.encoded_password or "",
                "max_participants": command.config.max_participants,
                "minimum_ready": command.config.minimum_ready,
                "vote_seconds": command.config.vote_seconds,
                "owner_id": command.owner_id,
                "session_digest": command.owner_session_digest,
            },
        )

    async def get(self, room_id: str) -> RoomRuntimeSnapshot | None:
        validate_room_id(room_id)
        result = await self._scripts.execute(
            ROOM_READ,
            keys=self._read_keys(room_id),
            args=(room_id,),
        )
        decoded = self._result(result)
        self._raise_rejection(decoded)
        return self._optional_snapshot(decoded.get("snapshot"))

    async def get_private_access_hash(self, room_id: str) -> str | None:
        validate_room_id(room_id)
        result = await self._scripts.execute(
            ROOM_PRIVATE_HASH_READ,
            keys=(RedisKeyspace.room_meta(room_id),),
            args=(),
        )
        decoded = self._result(result)
        self._raise_rejection(decoded)
        value = decoded.get("encoded_password")
        if value is None:
            return None
        if not isinstance(value, str) or not value.startswith("$argon2id$"):
            raise RedisProviderError("REDIS_RESPONSE_INVALID")
        return value

    async def join(self, command: JoinRoomRuntime) -> RoomMutationResult:
        return await self._mutate(
            command.room_id,
            command.request_id,
            "join",
            {
                "participant_id": command.participant_id,
                "actor_type": command.actor_type.value,
                "session_digest": command.session_digest,
                "expected_state_version": command.expected_state_version,
                "private_access_verified": command.private_access_verified,
            },
        )

    async def change_team(self, command: ChangeRoomTeam) -> RoomMutationResult:
        return await self._mutate(
            command.room_id,
            command.request_id,
            "change_team",
            {
                "participant_id": command.participant_id,
                "team": command.team.value,
                "expected_state_version": command.expected_state_version,
            },
        )

    async def change_identity(self, command: ChangeRoomIdentity) -> RoomMutationResult:
        return await self._mutate(
            command.room_id,
            command.request_id,
            "change_identity",
            {
                "participant_id": command.participant_id,
                "actor_type": command.actor_type.value,
                "expected_state_version": command.expected_state_version,
            },
        )

    async def set_ready(self, command: SetRoomReady) -> RoomMutationResult:
        return await self._mutate(
            command.room_id,
            command.request_id,
            "set_ready",
            {
                "participant_id": command.participant_id,
                "ready": command.ready,
                "expected_state_version": command.expected_state_version,
            },
        )

    async def change_vote_seconds(self, command: ChangeRoomVoteSeconds) -> RoomMutationResult:
        return await self._mutate(
            command.room_id,
            command.request_id,
            "change_vote_seconds",
            {
                "actor_id": command.actor_id,
                "vote_seconds": command.vote_seconds,
                "expected_state_version": command.expected_state_version,
            },
        )

    async def connect(self, command: ConnectRoomParticipant) -> RoomMutationResult:
        return await self._mutate(
            command.room_id,
            command.request_id,
            "connect",
            {
                "participant_id": command.participant_id,
                "session_digest": command.session_digest,
                "expected_state_version": command.expected_state_version,
            },
        )

    async def disconnect(self, command: DisconnectRoomParticipant) -> RoomMutationResult:
        return await self._mutate(
            command.room_id,
            command.request_id,
            "disconnect",
            {
                "participant_id": command.participant_id,
                "connection_generation": command.connection_generation,
                "expected_state_version": command.expected_state_version,
                "active_vote_turn": command.active_vote_turn,
            },
            active_vote_turn=command.active_vote_turn,
        )

    async def expire_disconnect(self, command: ExpireRoomDisconnect) -> RoomMutationResult:
        return await self._mutate(
            command.room_id,
            command.request_id,
            "expire_disconnect",
            {
                "participant_id": command.participant_id,
                "connection_generation": command.connection_generation,
                "expected_state_version": command.expected_state_version,
                "active_vote_turn": command.active_vote_turn,
            },
            active_vote_turn=command.active_vote_turn,
        )

    async def leave(self, command: LeaveRoomRuntime) -> RoomMutationResult:
        return await self._mutate(
            command.room_id,
            command.request_id,
            "leave",
            {
                "participant_id": command.participant_id,
                "expected_state_version": command.expected_state_version,
                "active_vote_turn": command.active_vote_turn,
            },
            active_vote_turn=command.active_vote_turn,
        )

    async def _mutate(
        self,
        room_id: str,
        request_id: str,
        operation: str,
        payload: Mapping[str, object],
        *,
        active_vote_turn: int | None = None,
    ) -> RoomMutationResult:
        result = await self._scripts.execute(
            ROOM_MUTATION,
            keys=self._mutation_keys(room_id, active_vote_turn),
            args=(
                room_id,
                operation,
                request_id,
                ROOM_REQUEST_DEDUPE_TTL_MS,
                ROOM_DISCONNECT_LEASE_MS,
                ROOM_CLOSED_TOMBSTONE_TTL_MS,
                VersionedJsonCodec.encode(payload),
            ),
        )
        decoded = self._result(result)
        self._raise_rejection(decoded)
        return self._mutation_result(decoded)

    @staticmethod
    def _read_keys(room_id: str) -> tuple[str, ...]:
        return (
            RedisKeyspace.room_meta(room_id),
            RedisKeyspace.room_participants(room_id),
            RedisKeyspace.room_ready(room_id),
            RedisKeyspace.room_connections(room_id),
        )

    @classmethod
    def _mutation_keys(cls, room_id: str, active_vote_turn: int | None) -> tuple[str, ...]:
        return (
            *cls._read_keys(room_id),
            RedisKeyspace.room_requests(room_id),
            RedisKeyspace.room_request_expiries(room_id),
            RedisKeyspace.room_closed(room_id),
            RedisKeyspace.room_votes(room_id, active_vote_turn),
            RedisKeyspace.room_vote_tally(room_id, active_vote_turn),
        )

    @staticmethod
    def _result(result: object) -> dict[str, object]:
        if not isinstance(result, (bytes, str)):
            raise RedisProviderError("REDIS_RESPONSE_INVALID")
        decoded = VersionedJsonCodec.decode(result)
        if not isinstance(decoded.get("ok"), bool):
            raise RedisProviderError("REDIS_RESPONSE_INVALID")
        return decoded

    @staticmethod
    def _raise_rejection(result: dict[str, object]) -> None:
        if result["ok"] is True:
            return
        error = result.get("error")
        if not isinstance(error, str):
            raise RedisProviderError("REDIS_RESPONSE_INVALID")
        raise RoomRuleViolation(error)

    @classmethod
    def _mutation_result(cls, value: dict[str, object]) -> RoomMutationResult:
        return RoomMutationResult(
            snapshot=cls._optional_snapshot(value.get("snapshot")),
            replayed=_optional_bool(value, "replayed", False),
            connection_generation=_optional_integer(value, "connection_generation"),
            disconnect_expires_at_ms=_optional_integer(value, "disconnect_expires_at_ms"),
            stale_connection=_optional_bool(value, "stale_connection", False),
            vote_removed=_optional_bool(value, "vote_removed", False),
            departure=cls._optional_departure(value.get("departure")),
        )

    @classmethod
    def _optional_snapshot(cls, value: object) -> RoomRuntimeSnapshot | None:
        if value is None:
            return None
        snapshot = _mapping(value)
        config_value = _mapping(snapshot["config"])
        participants_value = _list(snapshot["participants"])
        return RoomRuntimeSnapshot(
            room_id=_string(snapshot, "room_id"),
            config=RoomConfig(
                name=_string(config_value, "name"),
                visibility=RoomVisibility(_string(config_value, "visibility")),
                max_participants=_integer(config_value, "max_participants"),
                minimum_ready=_integer(config_value, "minimum_ready"),
                vote_seconds=_integer(config_value, "vote_seconds"),
            ),
            status=RoomStatus(_string(snapshot, "status")),
            owner_id=_optional_string(snapshot, "owner_id"),
            state_version=_integer(snapshot, "state_version"),
            participants=tuple(cls._participant(item) for item in participants_value),
            schema_version=_integer(snapshot, "schema_version"),
        )

    @staticmethod
    def _participant(value: object) -> RoomRuntimeParticipant:
        item = _mapping(value)
        return RoomRuntimeParticipant(
            participant_id=_string(item, "participant_id"),
            actor_type=ActorType(_string(item, "actor_type")),
            joined_order=_integer(item, "joined_order"),
            connected=_boolean(item, "connected"),
            team=Team(_string(item, "team")),
            ready=_boolean(item, "ready"),
        )

    @staticmethod
    def _optional_departure(value: object) -> DepartureResult | None:
        if value is None:
            return None
        item = _mapping(value)
        return DepartureResult(
            previous_owner_id=_optional_string(item, "previous_owner_id"),
            new_owner_id=_optional_string(item, "new_owner_id"),
            room_closed=_boolean(item, "room_closed"),
            game_termination=GameTermination(_string(item, "game_termination")),
        )


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RedisProviderError("REDIS_RESPONSE_INVALID")
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise RedisProviderError("REDIS_RESPONSE_INVALID")
    return cast(list[object], value)


def _string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise RedisProviderError("REDIS_RESPONSE_INVALID")
    return item


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str):
        raise RedisProviderError("REDIS_RESPONSE_INVALID")
    return item


def _integer(value: Mapping[str, object], key: str) -> int:
    item = value.get(key)
    if type(item) is not int:
        raise RedisProviderError("REDIS_RESPONSE_INVALID")
    return item


def _optional_integer(value: Mapping[str, object], key: str) -> int | None:
    item = value.get(key)
    if item is None:
        return None
    if type(item) is not int:
        raise RedisProviderError("REDIS_RESPONSE_INVALID")
    return item


def _boolean(value: Mapping[str, object], key: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise RedisProviderError("REDIS_RESPONSE_INVALID")
    return item


def _optional_bool(value: Mapping[str, object], key: str, default: bool) -> bool:
    item = value.get(key, default)
    if not isinstance(item, bool):
        raise RedisProviderError("REDIS_RESPONSE_INVALID")
    return item
