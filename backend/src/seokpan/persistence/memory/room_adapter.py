"""Deterministic in-memory Room runtime adapter."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Protocol

from seokpan.persistence.memory.session_adapter import ManualClock
from seokpan.room.application.runtime import (
    ROOM_CLOSED_TOMBSTONE_TTL_MS,
    ROOM_DISCONNECT_LEASE_MS,
    ROOM_REQUEST_DEDUPE_TTL_MS,
    ChangeRoomIdentity,
    ChangeRoomTeam,
    ChangeRoomVoteSeconds,
    CompleteRoomGame,
    ConnectRoomParticipant,
    CreateRoomRuntime,
    DisconnectRoomParticipant,
    ExpireRoomDisconnect,
    JoinRoomRuntime,
    LeaveRoomRuntime,
    RoomConnection,
    RoomMutationResult,
    RoomRuntimeParticipant,
    RoomRuntimeSnapshot,
    SetRoomReady,
    StartRoomGame,
    validate_room_id,
)
from seokpan.room.domain import (
    ActorType,
    DepartureResult,
    DisconnectReason,
    Participant,
    Room,
    RoomRuleViolation,
)


@dataclass(slots=True)
class _RoomState:
    room: Room
    encoded_password: str | None
    connections: dict[str, RoomConnection]


@dataclass(frozen=True, slots=True)
class _CachedResult:
    result: RoomMutationResult
    expires_at_ms: int
    fingerprint: str


class _RoomCommand(Protocol):
    @property
    def room_id(self) -> str: ...

    @property
    def request_id(self) -> str: ...


class InMemoryRoomRuntimeAdapter:
    """A Fake for contract tests; passing it is not Redis Provider evidence."""

    def __init__(self, clock: ManualClock) -> None:
        self._clock = clock
        self._rooms: dict[str, _RoomState] = {}
        self._tombstones: dict[str, int] = {}
        self._requests: dict[tuple[str, str], _CachedResult] = {}
        self._votes: dict[tuple[str, int], set[str]] = {}

    async def create(self, command: CreateRoomRuntime) -> RoomMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        self._purge_expired()
        if command.room_id in self._tombstones:
            raise RoomRuleViolation("ROOM_RECENTLY_CLOSED")
        if command.room_id in self._rooms:
            raise RoomRuleViolation("ROOM_ALREADY_EXISTS")

        owner = Participant(
            participant_id=command.owner_id,
            actor_type=ActorType.MEMBER,
            joined_order=1,
        )
        room = Room(config=command.config, owner=owner)
        state = _RoomState(
            room=room,
            encoded_password=command.encoded_password,
            connections={
                command.owner_id: RoomConnection(
                    participant_id=command.owner_id,
                    session_digest=command.owner_session_digest,
                    generation=1,
                    connected=True,
                    disconnect_expires_at_ms=None,
                )
            },
        )
        self._rooms[command.room_id] = state
        return self._remember(
            command,
            RoomMutationResult(
                snapshot=self._snapshot(command.room_id, state),
                connection_generation=1,
            ),
        )

    async def get(self, room_id: str) -> RoomRuntimeSnapshot | None:
        validate_room_id(room_id)
        self._purge_expired()
        state = self._rooms.get(room_id)
        return None if state is None else self._snapshot(room_id, state)

    async def list_rooms(self) -> tuple[RoomRuntimeSnapshot, ...]:
        self._purge_expired()
        return tuple(
            self._snapshot(room_id, state) for room_id, state in sorted(self._rooms.items())
        )

    async def get_private_access_hash(self, room_id: str) -> str | None:
        validate_room_id(room_id)
        self._purge_expired()
        state = self._rooms.get(room_id)
        if state is None:
            return None
        return state.encoded_password

    async def join(self, command: JoinRoomRuntime) -> RoomMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require_room(command.room_id)
        self._require_expected_version(state, command.expected_state_version)
        state.room.join(
            participant_id=command.participant_id,
            actor_type=command.actor_type,
            private_access_verified=command.private_access_verified,
        )
        state.connections[command.participant_id] = RoomConnection(
            participant_id=command.participant_id,
            session_digest=command.session_digest,
            generation=1,
            connected=True,
            disconnect_expires_at_ms=None,
        )
        return self._remember(
            command,
            RoomMutationResult(
                snapshot=self._snapshot(command.room_id, state),
                connection_generation=1,
            ),
        )

    async def change_team(self, command: ChangeRoomTeam) -> RoomMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require_room(command.room_id)
        self._require_expected_version(state, command.expected_state_version)
        state.room.change_team(participant_id=command.participant_id, team=command.team)
        return self._remember_result(command, state)

    async def change_identity(self, command: ChangeRoomIdentity) -> RoomMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require_room(command.room_id)
        self._require_expected_version(state, command.expected_state_version)
        state.room.change_identity(
            participant_id=command.participant_id,
            actor_type=command.actor_type,
        )
        return self._remember_result(command, state)

    async def set_ready(self, command: SetRoomReady) -> RoomMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require_room(command.room_id)
        self._require_expected_version(state, command.expected_state_version)
        state.room.set_ready(participant_id=command.participant_id, ready=command.ready)
        return self._remember_result(command, state)

    async def change_vote_seconds(self, command: ChangeRoomVoteSeconds) -> RoomMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require_room(command.room_id)
        self._require_expected_version(state, command.expected_state_version)
        state.room.change_vote_seconds(
            actor_id=command.actor_id,
            vote_seconds=command.vote_seconds,
        )
        return self._remember_result(command, state)

    async def start_game(self, command: StartRoomGame) -> RoomMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require_room(command.room_id)
        self._require_expected_version(state, command.expected_state_version)
        roster = state.room.start_game(actor_id=command.actor_id, game_id=command.game_id)
        return self._remember(
            command,
            RoomMutationResult(
                snapshot=self._snapshot(command.room_id, state),
                start_roster=roster,
            ),
        )

    async def complete_game(self, command: CompleteRoomGame) -> RoomMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require_room(command.room_id)
        self._require_expected_version(state, command.expected_state_version)
        state.room.complete_game(game_id=command.game_id)
        return self._remember_result(command, state)

    async def connect(self, command: ConnectRoomParticipant) -> RoomMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require_room(command.room_id)
        self._require_expected_version(state, command.expected_state_version)
        state.room.participant(command.participant_id)
        previous = state.connections.get(command.participant_id)
        generation = 1 if previous is None else previous.generation + 1
        state.connections[command.participant_id] = RoomConnection(
            participant_id=command.participant_id,
            session_digest=command.session_digest,
            generation=generation,
            connected=True,
            disconnect_expires_at_ms=None,
        )
        state.room.reconnect(participant_id=command.participant_id)
        return self._remember(
            command,
            RoomMutationResult(
                snapshot=self._snapshot(command.room_id, state),
                connection_generation=generation,
            ),
        )

    async def disconnect(self, command: DisconnectRoomParticipant) -> RoomMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require_room(command.room_id)
        connection = self._require_connection(state, command.participant_id)
        if connection.generation != command.connection_generation:
            return self._remember(
                command,
                RoomMutationResult(
                    snapshot=self._snapshot(command.room_id, state),
                    stale_connection=True,
                ),
            )
        self._require_expected_version(state, command.expected_state_version)
        if not connection.connected:
            return self._remember(
                command,
                RoomMutationResult(
                    snapshot=self._snapshot(command.room_id, state),
                    disconnect_expires_at_ms=connection.disconnect_expires_at_ms,
                ),
            )

        expires_at_ms = self._clock.now_ms + ROOM_DISCONNECT_LEASE_MS
        state.connections[command.participant_id] = replace(
            connection,
            connected=False,
            disconnect_expires_at_ms=expires_at_ms,
        )
        departure = state.room.disconnect(
            participant_id=command.participant_id,
            reason=DisconnectReason.PARTICIPANT_CONNECTION_LOST,
        )
        vote_removed = self._remove_vote(
            command.room_id,
            command.active_vote_turn,
            command.participant_id,
        )
        if departure.room_closed:
            return self._close(
                command,
                departure=departure,
                vote_removed=vote_removed,
            )
        return self._remember(
            command,
            RoomMutationResult(
                snapshot=self._snapshot(command.room_id, state),
                disconnect_expires_at_ms=expires_at_ms,
                vote_removed=vote_removed,
                departure=departure,
            ),
        )

    async def expire_disconnect(self, command: ExpireRoomDisconnect) -> RoomMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require_room(command.room_id)
        connection = self._require_connection(state, command.participant_id)
        if connection.generation != command.connection_generation or connection.connected:
            return self._remember(
                command,
                RoomMutationResult(
                    snapshot=self._snapshot(command.room_id, state),
                    stale_connection=True,
                ),
            )
        self._require_expected_version(state, command.expected_state_version)
        if (
            connection.disconnect_expires_at_ms is None
            or self._clock.now_ms < connection.disconnect_expires_at_ms
        ):
            raise RoomRuleViolation("DISCONNECT_LEASE_ACTIVE")

        departure = state.room.leave(participant_id=command.participant_id)
        state.connections.pop(command.participant_id, None)
        vote_removed = self._remove_vote(
            command.room_id,
            command.active_vote_turn,
            command.participant_id,
        )
        if departure.room_closed:
            return self._close(
                command,
                departure=departure,
                vote_removed=vote_removed,
            )
        return self._remember(
            command,
            RoomMutationResult(
                snapshot=self._snapshot(command.room_id, state),
                vote_removed=vote_removed,
                departure=departure,
            ),
        )

    async def leave(self, command: LeaveRoomRuntime) -> RoomMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require_room(command.room_id)
        self._require_expected_version(state, command.expected_state_version)
        departure = state.room.leave(participant_id=command.participant_id)
        state.connections.pop(command.participant_id, None)
        vote_removed = self._remove_vote(
            command.room_id,
            command.active_vote_turn,
            command.participant_id,
        )
        if departure.room_closed:
            return self._close(
                command,
                departure=departure,
                vote_removed=vote_removed,
            )
        return self._remember(
            command,
            RoomMutationResult(
                snapshot=self._snapshot(command.room_id, state),
                vote_removed=vote_removed,
                departure=departure,
            ),
        )

    def seed_current_vote(self, room_id: str, turn_no: int, participant_id: str) -> None:
        """Test observation seam used before the A-05c Vote adapter exists."""
        self._votes.setdefault((room_id, turn_no), set()).add(participant_id)

    def has_current_vote(self, room_id: str, turn_no: int, participant_id: str) -> bool:
        return participant_id in self._votes.get((room_id, turn_no), set())

    def has_tombstone(self, room_id: str) -> bool:
        self._purge_expired()
        return room_id in self._tombstones

    def _require_room(self, room_id: str) -> _RoomState:
        self._purge_expired()
        try:
            return self._rooms[room_id]
        except KeyError as error:
            raise RoomRuleViolation("ROOM_NOT_FOUND") from error

    @staticmethod
    def _require_connection(state: _RoomState, participant_id: str) -> RoomConnection:
        try:
            return state.connections[participant_id]
        except KeyError as error:
            raise RoomRuleViolation("CONNECTION_NOT_FOUND") from error

    @staticmethod
    def _require_expected_version(state: _RoomState, expected_state_version: int) -> None:
        if state.room.state_version != expected_state_version:
            raise RoomRuleViolation("STATE_VERSION_CONFLICT")

    def _remember_result(
        self,
        command: _RoomCommand,
        state: _RoomState,
    ) -> RoomMutationResult:
        return self._remember(
            command,
            RoomMutationResult(snapshot=self._snapshot(command.room_id, state)),
        )

    def _remember(
        self,
        command: _RoomCommand,
        result: RoomMutationResult,
    ) -> RoomMutationResult:
        self._requests[(command.room_id, command.request_id)] = _CachedResult(
            result=result,
            expires_at_ms=self._clock.now_ms + ROOM_REQUEST_DEDUPE_TTL_MS,
            fingerprint=self._fingerprint(command),
        )
        return result

    def _replay(self, command: _RoomCommand) -> RoomMutationResult | None:
        self._purge_expired()
        cached = self._requests.get((command.room_id, command.request_id))
        if cached is None:
            return None
        if cached.fingerprint != self._fingerprint(command):
            raise RoomRuleViolation("REQUEST_ID_CONFLICT")
        return replace(cached.result, replayed=True)

    def _close(
        self,
        command: _RoomCommand,
        *,
        departure: DepartureResult,
        vote_removed: bool,
    ) -> RoomMutationResult:
        self._rooms.pop(command.room_id, None)
        for vote_key in tuple(self._votes):
            if vote_key[0] == command.room_id:
                self._votes.pop(vote_key, None)
        self._tombstones[command.room_id] = self._clock.now_ms + ROOM_CLOSED_TOMBSTONE_TTL_MS
        return self._remember(
            command,
            RoomMutationResult(
                snapshot=None,
                vote_removed=vote_removed,
                departure=departure,
            ),
        )

    @staticmethod
    def _fingerprint(command: _RoomCommand) -> str:
        return hashlib.sha256(repr(command).encode(), usedforsecurity=False).hexdigest()

    def _remove_vote(
        self,
        room_id: str,
        turn_no: int | None,
        participant_id: str,
    ) -> bool:
        if turn_no is None:
            return False
        voters = self._votes.get((room_id, turn_no))
        if voters is None or participant_id not in voters:
            return False
        voters.remove(participant_id)
        if not voters:
            self._votes.pop((room_id, turn_no), None)
        return True

    def _purge_expired(self) -> None:
        now_ms = self._clock.now_ms
        for room_id, expires_at_ms in tuple(self._tombstones.items()):
            if expires_at_ms <= now_ms:
                self._tombstones.pop(room_id, None)
        for key, cached in tuple(self._requests.items()):
            if cached.expires_at_ms <= now_ms:
                self._requests.pop(key, None)

    @staticmethod
    def _snapshot(room_id: str, state: _RoomState) -> RoomRuntimeSnapshot:
        room = state.room
        participants = tuple(
            RoomRuntimeParticipant(
                participant_id=item.participant_id,
                actor_type=item.actor_type,
                joined_order=item.joined_order,
                connected=item.connected,
                team=item.team,
                ready=item.ready,
            )
            for item in room.participants
        )
        return RoomRuntimeSnapshot(
            room_id=room_id,
            config=room.config,
            status=room.status,
            owner_id=room.owner_id,
            state_version=room.state_version,
            participants=participants,
            game_id=room.game_id,
        )
