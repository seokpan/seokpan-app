"""Application use cases joining Room, Game persistence, and Vote runtime."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from seokpan.game.application.persistence import (
    GameParticipantRecord,
    GamePersistencePort,
    PersistenceOutcome,
    StartGameCommand,
)
from seokpan.game.domain import Coordinate, GameParticipantRole, Stone
from seokpan.identity.application import SessionActorType, SessionRecord
from seokpan.room.application import (
    RoomApplicationService,
    RoomParticipation,
    RoomRuntimeSnapshot,
)
from seokpan.room.domain import ParticipantRole as RoomParticipantRole
from seokpan.room.domain import RoomRuleViolation, Team
from seokpan.vote.application import (
    CastRuntimeVote,
    InitializeVoteRuntime,
    RemoveRuntimeVote,
    VoteRuntimePort,
    VoteRuntimeSnapshot,
)
from seokpan.vote.domain import ParticipantRole as VoteParticipantRole
from seokpan.vote.domain import Voter, VoteRuleViolation


class MillisecondClock(Protocol):
    @property
    def now_ms(self) -> int: ...


@dataclass(frozen=True, slots=True)
class GameApplicationSnapshot:
    room: RoomRuntimeSnapshot
    game: VoteRuntimeSnapshot
    viewer_participant_id: str
    now_ms: int
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _AllocatedStart:
    game_id: str
    expected_state_version: int
    started_at_ms: int


class GameApplicationService:
    """Coordinate retry-safe Room, persistence, and Vote-runtime Game startup."""

    def __init__(
        self,
        *,
        rooms: RoomApplicationService,
        games: GamePersistencePort,
        votes: VoteRuntimePort,
        clock: MillisecondClock,
    ) -> None:
        self._rooms = rooms
        self._games = games
        self._votes = votes
        self._clock = clock
        self._starts: dict[tuple[str, str], _AllocatedStart] = {}

    async def start_game(
        self,
        *,
        session: SessionRecord,
        room_id: str,
        request_id: str,
        expected_state_version: int,
    ) -> GameApplicationSnapshot:
        participation = self._require_participation(session)
        if participation.room_id != room_id:
            raise RoomRuleViolation("SESSION_NOT_IN_ROOM")
        key = (participation.room_id, request_id)
        allocated = self._starts.get(key)
        if allocated is None:
            allocated = _AllocatedStart(
                str(uuid4()),
                expected_state_version,
                self._clock.now_ms,
            )
            self._starts[key] = allocated
        elif allocated.expected_state_version != expected_state_version:
            raise RoomRuleViolation("REQUEST_ID_CONFLICT")

        room_result = await self._rooms.start_game(
            session=session,
            request_id=request_id,
            game_id=allocated.game_id,
            expected_state_version=expected_state_version,
        )
        room = room_result.snapshot
        roster = room_result.start_roster
        if room is None or roster is None:
            raise RoomRuleViolation("GAME_START_RESULT_INVALID")

        player_entries = tuple(
            entry for entry in roster.entries if entry.role is RoomParticipantRole.PLAYER
        )
        persistence_outcome = await self._games.start_game(
            StartGameCommand(
                game_id=allocated.game_id,
                room_id=room.room_id,
                voting_time_seconds=room.config.vote_seconds,
                started_at=datetime.fromtimestamp(allocated.started_at_ms / 1000, UTC),
                participants=tuple(
                    self._persistence_participant(item.participant_id, item.team)
                    for item in player_entries
                ),
            )
        )
        vote_result = await self._votes.initialize(
            InitializeVoteRuntime(
                room_id=room.room_id,
                request_id=request_id,
                game_id=allocated.game_id,
                participants=tuple(
                    Voter(
                        participant_id=item.participant_id,
                        team=_stone(item.team),
                        role=VoteParticipantRole.PLAYER,
                        connected=_connected(room, item.participant_id),
                    )
                    for item in player_entries
                ),
                deadline_ms=allocated.started_at_ms + room.config.vote_seconds * 1000,
                expected_state_version=1,
            )
        )
        replayed = (
            room_result.replayed
            or persistence_outcome is PersistenceOutcome.UNCHANGED
            or vote_result.replayed
        )
        return GameApplicationSnapshot(
            room,
            vote_result.snapshot,
            participation.participant_id,
            self._clock.now_ms,
            replayed,
        )

    async def get_game(
        self,
        *,
        session: SessionRecord,
        game_id: str,
    ) -> GameApplicationSnapshot:
        participation, room, runtime = await self._current(session, game_id)
        return GameApplicationSnapshot(
            room, runtime, participation.participant_id, self._clock.now_ms
        )

    async def current_state_reference(
        self,
        session: SessionRecord,
    ) -> tuple[int | None, str | None]:
        participation = self._require_participation(session)
        room = await self._rooms.get(participation.room_id)
        runtime = await self._votes.get(participation.room_id)
        if runtime is not None:
            return runtime.state_version, f"/api/v1/games/{runtime.game_id}"
        return (None if room is None else room.state_version), None

    async def cast_vote(
        self,
        *,
        session: SessionRecord,
        game_id: str,
        turn_no: int,
        request_id: str,
        coordinate: str,
        expected_state_version: int,
    ) -> GameApplicationSnapshot:
        participation, room, runtime = await self._current(session, game_id)
        self._require_voter(runtime, participation.participant_id)
        result = await self._votes.cast_vote(
            CastRuntimeVote(
                room_id=room.room_id,
                request_id=request_id,
                game_id=game_id,
                turn_no=turn_no,
                participant_id=participation.participant_id,
                coordinate=Coordinate.parse(coordinate),
                expected_state_version=expected_state_version,
            )
        )
        return GameApplicationSnapshot(
            room,
            result.snapshot,
            participation.participant_id,
            self._clock.now_ms,
            result.replayed,
        )

    async def remove_vote(
        self,
        *,
        session: SessionRecord,
        game_id: str,
        turn_no: int,
        request_id: str,
        expected_state_version: int,
    ) -> GameApplicationSnapshot:
        participation, room, runtime = await self._current(session, game_id)
        self._require_voter(runtime, participation.participant_id)
        result = await self._votes.remove_vote(
            RemoveRuntimeVote(
                room_id=room.room_id,
                request_id=request_id,
                game_id=game_id,
                turn_no=turn_no,
                participant_id=participation.participant_id,
                expected_state_version=expected_state_version,
            )
        )
        return GameApplicationSnapshot(
            room,
            result.snapshot,
            participation.participant_id,
            self._clock.now_ms,
            result.replayed,
        )

    async def _current(
        self,
        session: SessionRecord,
        game_id: str,
    ) -> tuple[RoomParticipation, RoomRuntimeSnapshot, VoteRuntimeSnapshot]:
        participation = self._require_participation(session)
        room = await self._rooms.get(participation.room_id)
        if room is None:
            raise RoomRuleViolation("ROOM_NOT_FOUND")
        if room.game_id != game_id:
            raise RoomRuleViolation("GAME_NOT_IN_CURRENT_ROOM")
        runtime = await self._votes.get(room.room_id)
        if runtime is None or runtime.game_id != game_id:
            raise RoomRuleViolation("GAME_RUNTIME_NOT_FOUND")
        return participation, room, runtime

    def _require_participation(self, session: SessionRecord) -> RoomParticipation:
        participation = self._rooms.participation(session.session_digest)
        if participation is None:
            raise RoomRuleViolation("SESSION_NOT_IN_ROOM")
        return participation

    @staticmethod
    def _require_voter(runtime: VoteRuntimeSnapshot, participant_id: str) -> None:
        if not any(item.participant_id == participant_id for item in runtime.participants):
            raise VoteRuleViolation("PLAYER_REQUIRED")

    def _persistence_participant(self, participant_id: str, team: Team) -> GameParticipantRecord:
        identity = self._rooms.participant_identity(participant_id)
        if identity is None:
            raise RoomRuleViolation("PARTICIPANT_IDENTITY_NOT_FOUND")
        if identity.actor_type is SessionActorType.MEMBER:
            return GameParticipantRecord(
                participant_id=participant_id,
                team=_stone(team),
                role=GameParticipantRole.PLAYER,
                member_id=int(identity.actor_id),
            )
        number = int(hashlib.sha256(identity.actor_id.encode("utf-8")).hexdigest()[:8], 16) % 10_000
        return GameParticipantRecord(
            participant_id=participant_id,
            team=_stone(team),
            role=GameParticipantRole.PLAYER,
            guest_label=f"Guest-{number:04d}",
        )


def _stone(team: Team) -> Stone:
    if team is Team.NONE:
        raise RoomRuleViolation("PLAYER_TEAM_REQUIRED")
    return Stone(team.value)


def _connected(room: RoomRuntimeSnapshot, participant_id: str) -> bool:
    return next(
        item.connected for item in room.participants if item.participant_id == participant_id
    )
