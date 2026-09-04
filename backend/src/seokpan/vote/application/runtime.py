"""Provider-neutral Vote, Turn, and resolver runtime contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from seokpan.game.domain import BoardCell, Coordinate, EndReason, GameStatus, Stone
from seokpan.vote.domain import (
    TurnClosure,
    TurnResolution,
    TurnResultKind,
    TurnStatus,
    Vote,
    Voter,
    VoteRuleViolation,
    VoteTally,
)

VOTE_RUNTIME_SCHEMA_VERSION = 2
RESOLVER_LEASE_MS = 5 * 1000
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _identifier(value: str, *, code: str) -> None:
    if _SAFE_ID.fullmatch(value) is None:
        raise VoteRuleViolation(code)


def _positive(value: int, *, code: str) -> None:
    if value < 1:
        raise VoteRuleViolation(code)


def _base(room_id: str, request_id: str, game_id: str) -> None:
    _identifier(room_id, code="INVALID_ROOM_ID")
    _identifier(request_id, code="INVALID_REQUEST_ID")
    _identifier(game_id, code="INVALID_GAME_ID")


@dataclass(frozen=True, slots=True)
class InitializeVoteRuntime:
    room_id: str
    request_id: str
    game_id: str
    participants: tuple[Voter, ...]
    deadline_ms: int
    expected_state_version: int

    def __post_init__(self) -> None:
        _base(self.room_id, self.request_id, self.game_id)
        _positive(self.expected_state_version, code="INVALID_STATE_VERSION")
        if self.deadline_ms < 0:
            raise VoteRuleViolation("INVALID_DEADLINE")


@dataclass(frozen=True, slots=True)
class CastRuntimeVote:
    room_id: str
    request_id: str
    game_id: str
    turn_no: int
    participant_id: str
    coordinate: Coordinate
    expected_state_version: int

    def __post_init__(self) -> None:
        _base(self.room_id, self.request_id, self.game_id)
        _positive(self.turn_no, code="INVALID_TURN_NUMBER")
        _identifier(self.participant_id, code="INVALID_PARTICIPANT_ID")
        _positive(self.expected_state_version, code="INVALID_STATE_VERSION")


@dataclass(frozen=True, slots=True)
class RemoveRuntimeVote:
    room_id: str
    request_id: str
    game_id: str
    turn_no: int
    participant_id: str
    expected_state_version: int

    def __post_init__(self) -> None:
        _base(self.room_id, self.request_id, self.game_id)
        _positive(self.turn_no, code="INVALID_TURN_NUMBER")
        _identifier(self.participant_id, code="INVALID_PARTICIPANT_ID")
        _positive(self.expected_state_version, code="INVALID_STATE_VERSION")


@dataclass(frozen=True, slots=True)
class CloseRuntimeTurn:
    room_id: str
    request_id: str
    game_id: str
    turn_no: int
    expected_state_version: int
    next_deadline_ms: int | None = None

    def __post_init__(self) -> None:
        _base(self.room_id, self.request_id, self.game_id)
        _positive(self.turn_no, code="INVALID_TURN_NUMBER")
        _positive(self.expected_state_version, code="INVALID_STATE_VERSION")


@dataclass(frozen=True, slots=True)
class AcquireRuntimeResolver:
    room_id: str
    request_id: str
    game_id: str
    turn_no: int
    resolution_id: str
    expected_state_version: int

    def __post_init__(self) -> None:
        _base(self.room_id, self.request_id, self.game_id)
        _positive(self.turn_no, code="INVALID_TURN_NUMBER")
        _identifier(self.resolution_id, code="INVALID_RESOLUTION_ID")
        _positive(self.expected_state_version, code="INVALID_STATE_VERSION")


@dataclass(frozen=True, slots=True)
class ApplyRuntimeResolution:
    room_id: str
    request_id: str
    game_id: str
    turn_no: int
    resolution_id: str
    resolution: TurnResolution
    expected_state_version: int
    persistence_confirmed: bool
    next_deadline_ms: int | None = None

    def __post_init__(self) -> None:
        _base(self.room_id, self.request_id, self.game_id)
        _positive(self.turn_no, code="INVALID_TURN_NUMBER")
        _identifier(self.resolution_id, code="INVALID_RESOLUTION_ID")
        _positive(self.expected_state_version, code="INVALID_STATE_VERSION")
        if not self.persistence_confirmed:
            raise VoteRuleViolation("PERSISTENCE_CONFIRMATION_REQUIRED")
        if self.resolution.game_id != self.game_id or self.resolution.turn_no != self.turn_no:
            raise VoteRuleViolation("RESOLUTION_MISMATCH")
        move = self.resolution.applied_move
        move_resolution = (
            self.resolution.result is TurnResultKind.MOVE_APPLIED
            and self.resolution.status is TurnStatus.MOVE_APPLIED
            and self.resolution.selected_coordinate is not None
            and move is not None
            and move.team is self.resolution.team
            and move.coordinate == self.resolution.selected_coordinate
        )
        joint_loss_resolution = (
            self.resolution.result is TurnResultKind.JOINT_LOSS
            and self.resolution.status is TurnStatus.PASSED
            and self.resolution.selected_coordinate is None
            and move is None
            and self.resolution.end_reason is EndReason.JOINT_LOSS
        )
        if not move_resolution and not joint_loss_resolution:
            raise VoteRuleViolation("RESOLUTION_MISMATCH")
        if self.resolution.end_reason is None and self.next_deadline_ms is None:
            raise VoteRuleViolation("INVALID_NEXT_DEADLINE")


@dataclass(frozen=True, slots=True)
class ResolverLease:
    resolution_id: str
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class VoteRuntimeSnapshot:
    room_id: str
    game_id: str
    state_version: int
    turn_no: int
    turn_status: TurnStatus
    current_team: Stone
    deadline_ms: int | None
    consecutive_passes: int
    move_no: int
    game_status: GameStatus
    end_reason: EndReason | None
    participants: tuple[Voter, ...]
    votes: tuple[Vote, ...]
    tally: tuple[VoteTally, ...]
    candidates: tuple[Coordinate, ...]
    occupied_cells: tuple[BoardCell, ...]
    resolver: ResolverLease | None
    valid_voter_count: int | None = None
    schema_version: int = VOTE_RUNTIME_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class VoteMutationResult:
    snapshot: VoteRuntimeSnapshot
    replayed: bool = False
    closure: TurnClosure | None = None
    resolution: TurnResolution | None = None
    valid_voter_count: int | None = None


class VoteRuntimePort(Protocol):
    async def initialize(self, command: InitializeVoteRuntime) -> VoteMutationResult: ...

    async def get(self, room_id: str) -> VoteRuntimeSnapshot | None: ...

    async def cast_vote(self, command: CastRuntimeVote) -> VoteMutationResult: ...

    async def remove_vote(self, command: RemoveRuntimeVote) -> VoteMutationResult: ...

    async def close_turn(self, command: CloseRuntimeTurn) -> VoteMutationResult: ...

    async def acquire_resolver(self, command: AcquireRuntimeResolver) -> VoteMutationResult: ...

    async def apply_resolution(self, command: ApplyRuntimeResolution) -> VoteMutationResult: ...
