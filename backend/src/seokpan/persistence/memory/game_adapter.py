"""Deterministic in-memory implementation of the Game persistence write port."""

from __future__ import annotations

from seokpan.game.application import (
    FinalizeGameCommand,
    OfficialMoveRecord,
    PersistenceOutcome,
    PersistenceRuleViolation,
    StartGameCommand,
)


class InMemoryGamePersistenceAdapter:
    """A Headless fake; passing it is not MariaDB integration evidence."""

    def __init__(self) -> None:
        self.games: dict[str, StartGameCommand] = {}
        self.moves: dict[tuple[str, int], OfficialMoveRecord] = {}
        self.results: dict[str, FinalizeGameCommand] = {}

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
