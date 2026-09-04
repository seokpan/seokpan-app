"""Deterministic in-memory implementation of the Game persistence write port."""

from __future__ import annotations

from seokpan.game.application import (
    FinalizeGameCommand,
    GamePersistenceSnapshot,
    OfficialMoveRecord,
    PersistenceOutcome,
    PersistenceRuleViolation,
    StartGameCommand,
)
from seokpan.game.domain import GameParticipantRole, GameParticipantSnapshot


class InMemoryGamePersistenceAdapter:
    """A Headless fake; passing it is not MariaDB integration evidence."""

    def __init__(self, member_ratings: dict[int, int] | None = None) -> None:
        self.games: dict[str, StartGameCommand] = {}
        self.moves: dict[tuple[str, int], OfficialMoveRecord] = {}
        self.results: dict[str, FinalizeGameCommand] = {}
        self.member_ratings = {} if member_ratings is None else dict(member_ratings)

    async def start_game(self, command: StartGameCommand) -> PersistenceOutcome:
        existing = self.games.get(command.game_id)
        if existing is None:
            self.games[command.game_id] = command
            return PersistenceOutcome.CREATED
        if existing == command:
            return PersistenceOutcome.UNCHANGED
        raise PersistenceRuleViolation("GAME_START_CONFLICT")

    async def append_move(self, command: OfficialMoveRecord) -> PersistenceOutcome:
        if command.game_id not in self.games:
            raise PersistenceRuleViolation("GAME_NOT_FOUND")
        key = (command.game_id, command.move_no)
        existing = self.moves.get(key)
        if existing is None:
            self.moves[key] = command
            return PersistenceOutcome.CREATED
        if existing == command:
            return PersistenceOutcome.UNCHANGED
        raise PersistenceRuleViolation("MOVE_CONFLICT")

    async def finalize_game(self, command: FinalizeGameCommand) -> PersistenceOutcome:
        game_id = command.result.game_id
        if game_id not in self.games:
            raise PersistenceRuleViolation("GAME_NOT_FOUND")
        existing = self.results.get(game_id)
        if existing is None:
            self.results[game_id] = command
            return PersistenceOutcome.CREATED
        if existing == command:
            return PersistenceOutcome.UNCHANGED
        raise PersistenceRuleViolation("GAME_RESULT_CONFLICT")

    async def load_game(self, game_id: str) -> GamePersistenceSnapshot | None:
        start = self.games.get(game_id)
        if start is None:
            return None
        prior_ratings = {
            item.member_id: item.rating_before
            for item in (
                ()
                if game_id not in self.results
                else self.results[game_id].result.rating_adjustments
            )
        }
        participants: list[GameParticipantSnapshot] = []
        for item in start.participants:
            rating = None
            if item.member_id is not None:
                rating = prior_ratings.get(item.member_id, self.member_ratings.get(item.member_id))
                if rating is None:
                    raise PersistenceRuleViolation("MEMBER_RATING_NOT_FOUND")
            participants.append(
                GameParticipantSnapshot(
                    participant_id=item.participant_id,
                    team=item.team,
                    role=GameParticipantRole.PLAYER,
                    member_id=item.member_id,
                    rating=rating,
                )
            )
        moves = tuple(
            value
            for key, value in sorted(self.moves.items(), key=lambda item: item[0][1])
            if key[0] == game_id
        )
        return GamePersistenceSnapshot(start, tuple(participants), moves)

    async def get_move(self, game_id: str, turn_no: int) -> OfficialMoveRecord | None:
        return self.moves.get((game_id, turn_no))

    async def result_matches(self, command: FinalizeGameCommand) -> bool:
        existing = self.results.get(command.result.game_id)
        if existing is None:
            return False
        if existing == command:
            return True
        raise PersistenceRuleViolation("GAME_RESULT_CONFLICT")
