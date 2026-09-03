"""Provider-neutral write contract for persistent Game history."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from seokpan.game.domain import Coordinate, GameParticipantRole, GameResult, GameStatus, Stone

_GUEST_LABEL_PATTERN = re.compile(r"Guest-[0-9]{4}")


class PersistenceOutcome(StrEnum):
    CREATED = "CREATED"
    UNCHANGED = "UNCHANGED"


class PersistenceRuleViolation(ValueError):
    """A stable rejection which must leave persistent state unchanged."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def require_uuid4(value: str, *, code: str) -> None:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise PersistenceRuleViolation(code) from error
    if parsed.version != 4 or str(parsed) != value:
        raise PersistenceRuleViolation(code)


@dataclass(frozen=True, slots=True)
class GameParticipantRecord:
    participant_id: str
    team: Stone
    role: GameParticipantRole = GameParticipantRole.PLAYER
    member_id: int | None = None
    guest_label: str | None = None

    def __post_init__(self) -> None:
        require_uuid4(self.participant_id, code="INVALID_PARTICIPANT_ID")
        if self.team is Stone.EMPTY:
            raise PersistenceRuleViolation("INVALID_PARTICIPANT_TEAM")
        if self.role is not GameParticipantRole.PLAYER:
            raise PersistenceRuleViolation("ONLY_PLAYER_SNAPSHOT_ALLOWED")
        if self.member_id is None:
            if self.guest_label is None or _GUEST_LABEL_PATTERN.fullmatch(self.guest_label) is None:
                raise PersistenceRuleViolation("GUEST_LABEL_REQUIRED")
        elif self.member_id <= 0 or self.guest_label is not None:
            raise PersistenceRuleViolation("INVALID_MEMBER_PARTICIPANT")


@dataclass(frozen=True, slots=True)
class StartGameCommand:
    game_id: str
    room_id: str
    voting_time_seconds: int
    started_at: datetime
    participants: tuple[GameParticipantRecord, ...]

    def __post_init__(self) -> None:
        require_uuid4(self.game_id, code="INVALID_GAME_ID")
        require_uuid4(self.room_id, code="INVALID_ROOM_ID")
        if self.voting_time_seconds not in {5, 10, 15, 30}:
            raise PersistenceRuleViolation("INVALID_VOTING_TIME")
        if not self.participants:
            raise PersistenceRuleViolation("PARTICIPANTS_REQUIRED")
        participant_ids = tuple(item.participant_id for item in self.participants)
        if len(participant_ids) != len(set(participant_ids)):
            raise PersistenceRuleViolation("DUPLICATE_PARTICIPANT")
        member_ids = tuple(
            item.member_id for item in self.participants if item.member_id is not None
        )
        if len(member_ids) != len(set(member_ids)):
            raise PersistenceRuleViolation("DUPLICATE_MEMBER")
        guest_labels = tuple(
            item.guest_label for item in self.participants if item.guest_label is not None
        )
        if len(guest_labels) != len(set(guest_labels)):
            raise PersistenceRuleViolation("DUPLICATE_GUEST_LABEL")


@dataclass(frozen=True, slots=True)
class OfficialMoveRecord:
    game_id: str
    turn_no: int
    move_no: int
    team: Stone
    coordinate: Coordinate
    final_vote_count: int
    valid_voter_count: int
    confirmed_at: datetime

    def __post_init__(self) -> None:
        require_uuid4(self.game_id, code="INVALID_GAME_ID")
        if self.turn_no <= 0 or self.move_no <= 0:
            raise PersistenceRuleViolation("INVALID_MOVE_SEQUENCE")
        if self.team is Stone.EMPTY:
            raise PersistenceRuleViolation("INVALID_MOVE_TEAM")
        if not 0 <= self.final_vote_count <= self.valid_voter_count:
            raise PersistenceRuleViolation("INVALID_VOTE_COUNT")


@dataclass(frozen=True, slots=True)
class FinalizeGameCommand:
    result: GameResult
    ended_at: datetime

    def __post_init__(self) -> None:
        require_uuid4(self.result.game_id, code="INVALID_GAME_ID")
        if self.result.status is GameStatus.ACTIVE:
            raise PersistenceRuleViolation("GAME_NOT_FINISHED")
        if not self.result.stats_eligible and self.result.rating_adjustments:
            raise PersistenceRuleViolation("INELIGIBLE_STATS_ADJUSTMENT")
        member_ids = tuple(item.member_id for item in self.result.rating_adjustments)
        if len(member_ids) != len(set(member_ids)):
            raise PersistenceRuleViolation("DUPLICATE_RATING_ADJUSTMENT")


class GamePersistencePort(Protocol):
    async def start_game(self, command: StartGameCommand) -> PersistenceOutcome: ...

    async def append_move(self, command: OfficialMoveRecord) -> PersistenceOutcome: ...

    async def finalize_game(self, command: FinalizeGameCommand) -> PersistenceOutcome: ...
