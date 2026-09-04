"""Explicit Headless fakes for the provider-neutral Turn resolution runner."""

from __future__ import annotations

from seokpan.game.application import (
    DueTurn,
    TieSelectionRecord,
    TurnFinalizationApproval,
)
from seokpan.vote.application import VoteRuntimeSnapshot


class InMemoryDueTurnSource:
    def __init__(self, values: tuple[DueTurn, ...] = ()) -> None:
        self.values = values

    async def due_turns(self, *, now_ms: int, limit: int) -> tuple[DueTurn, ...]:
        del now_ms
        return self.values[:limit]


class InMemoryTurnFinalizationGate:
    def __init__(
        self,
        approval: TurnFinalizationApproval = TurnFinalizationApproval.ALLOWED,
    ) -> None:
        self.approval = approval

    async def assess(
        self,
        *,
        due_turn: DueTurn,
        snapshot: VoteRuntimeSnapshot,
    ) -> TurnFinalizationApproval:
        del due_turn, snapshot
        return self.approval


class InMemoryTieSelector:
    def __init__(self, selected_coordinate: str) -> None:
        self.selected_coordinate = selected_coordinate
        self.calls: list[tuple[str, int, tuple[str, ...]]] = []

    async def select(
        self,
        *,
        game_id: str,
        turn_no: int,
        candidates: tuple[str, ...],
    ) -> str:
        self.calls.append((game_id, turn_no, candidates))
        return self.selected_coordinate


class InMemoryTieSelectionAudit:
    def __init__(self) -> None:
        self.records: list[TieSelectionRecord] = []

    async def record(self, value: TieSelectionRecord) -> None:
        if value not in self.records:
            self.records.append(value)
