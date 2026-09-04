"""Provider-neutral runner for closing due Turns and confirming official results."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from seokpan.game.application.persistence import (
    FinalizeGameCommand,
    GamePersistencePort,
    GamePersistenceSnapshot,
    OfficialMoveRecord,
    PersistenceRuleViolation,
)
from seokpan.game.domain import EndReason, Game, GameResultService, GameStatus
from seokpan.room.application import CompleteRoomGame, RoomRuntimePort, RoomRuntimeSnapshot
from seokpan.room.domain import RoomStatus
from seokpan.vote.application import (
    AcquireRuntimeResolver,
    ApplyRuntimeResolution,
    CloseRuntimeTurn,
    VoteRuntimePort,
    VoteRuntimeSnapshot,
)
from seokpan.vote.domain import (
    TurnResolution,
    TurnResultKind,
    TurnStatus,
    VoteRuleViolation,
    VoteTurnGame,
)


class TurnFinalizationApproval(StrEnum):
    ALLOWED = "ALLOWED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class TurnProcessingStatus(StrEnum):
    PASS = "PASS"
    MOVE = "MOVE"
    GAME_ENDED = "GAME_ENDED"
    NOT_DUE = "NOT_DUE"
    STALE = "STALE"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    RESOLVER_BUSY = "RESOLVER_BUSY"
    RETRY_REQUIRED = "RETRY_REQUIRED"


@dataclass(frozen=True, slots=True)
class DueTurn:
    room_id: str
    game_id: str
    turn_no: int


@dataclass(frozen=True, slots=True)
class TieSelectionRecord:
    game_id: str
    turn_no: int
    candidates: tuple[str, ...]
    selected_coordinate: str


@dataclass(frozen=True, slots=True)
class TurnProcessingResult:
    due_turn: DueTurn
    status: TurnProcessingStatus
    resolution: TurnResolution | None = None


class MillisecondClock(Protocol):
    @property
    def now_ms(self) -> int: ...


class DueTurnSource(Protocol):
    async def due_turns(self, *, now_ms: int, limit: int) -> tuple[DueTurn, ...]: ...


class TurnFinalizationGate(Protocol):
    async def assess(
        self,
        *,
        due_turn: DueTurn,
        snapshot: VoteRuntimeSnapshot,
    ) -> TurnFinalizationApproval: ...


class TieSelector(Protocol):
    async def select(
        self,
        *,
        game_id: str,
        turn_no: int,
        candidates: tuple[str, ...],
    ) -> str: ...


class TieSelectionAuditPort(Protocol):
    async def record(self, value: TieSelectionRecord) -> None: ...


class TurnResolutionRunner:
    """Close due Turns without making client traffic the scheduler or authority."""

    def __init__(
        self,
        *,
        due_turns: DueTurnSource,
        finalization_gate: TurnFinalizationGate,
        tie_selector: TieSelector,
        tie_audit: TieSelectionAuditPort,
        votes: VoteRuntimePort,
        games: GamePersistencePort,
        rooms: RoomRuntimePort,
        clock: MillisecondClock,
        runner_id: str,
    ) -> None:
        if not runner_id:
            raise ValueError("INVALID_RUNNER_ID")
        self._due_turns = due_turns
        self._finalization_gate = finalization_gate
        self._tie_selector = tie_selector
        self._tie_audit = tie_audit
        self._votes = votes
        self._games = games
        self._rooms = rooms
        self._clock = clock
        self._runner_id = hashlib.sha256(runner_id.encode()).hexdigest()[:12]

    async def run_once(self, *, limit: int = 100) -> tuple[TurnProcessingResult, ...]:
        if limit < 1:
            raise ValueError("INVALID_DUE_TURN_LIMIT")
        due = await self._due_turns.due_turns(now_ms=self._clock.now_ms, limit=limit)
        return tuple([await self.process(item) for item in due])

    async def process(self, due_turn: DueTurn) -> TurnProcessingResult:
        snapshot = await self._votes.get(due_turn.room_id)
        if snapshot is None or snapshot.game_id != due_turn.game_id:
            return TurnProcessingResult(due_turn, TurnProcessingStatus.STALE)
        if snapshot.turn_no != due_turn.turn_no:
            return TurnProcessingResult(due_turn, TurnProcessingStatus.STALE)
        if (
            snapshot.game_status is GameStatus.FINISHED
            and snapshot.end_reason is not None
            and snapshot.turn_status in {TurnStatus.MOVE_APPLIED, TurnStatus.PASSED}
        ):
            if not await self._games.game_is_finalized(due_turn.game_id):
                return TurnProcessingResult(due_turn, TurnProcessingStatus.RETRY_REQUIRED)
            await self._complete_room(due_turn)
            return TurnProcessingResult(due_turn, TurnProcessingStatus.GAME_ENDED)
        if snapshot.deadline_ms is not None and self._clock.now_ms < snapshot.deadline_ms:
            return TurnProcessingResult(due_turn, TurnProcessingStatus.NOT_DUE)
        if snapshot.turn_status is TurnStatus.VOTING:
            approval = await self._finalization_gate.assess(
                due_turn=due_turn,
                snapshot=snapshot,
            )
            if approval is not TurnFinalizationApproval.ALLOWED:
                return TurnProcessingResult(due_turn, TurnProcessingStatus.RECOVERY_REQUIRED)
            room = await self._require_playing_room(due_turn)
            assert snapshot.deadline_ms is not None
            closed = await self._votes.close_turn(
                CloseRuntimeTurn(
                    room_id=due_turn.room_id,
                    request_id=_stable_id("close", due_turn),
                    game_id=due_turn.game_id,
                    turn_no=due_turn.turn_no,
                    expected_state_version=snapshot.state_version,
                    next_deadline_ms=snapshot.deadline_ms + room.config.vote_seconds * 1000,
                )
            )
            if closed.closure is None:
                raise VoteRuleViolation("TURN_CLOSURE_MISSING")
            if closed.closure.result is TurnResultKind.PASSED:
                return TurnProcessingResult(due_turn, TurnProcessingStatus.PASS)
            snapshot = closed.snapshot
        elif snapshot.turn_status is not TurnStatus.RESOLVING:
            return TurnProcessingResult(due_turn, TurnProcessingStatus.STALE)

        try:
            leased = await self._votes.acquire_resolver(
                AcquireRuntimeResolver(
                    room_id=due_turn.room_id,
                    request_id=_stable_id(
                        f"lease-{self._runner_id}-{self._clock.now_ms}", due_turn
                    ),
                    game_id=due_turn.game_id,
                    turn_no=due_turn.turn_no,
                    resolution_id=self._resolution_id(due_turn),
                    expected_state_version=snapshot.state_version,
                )
            )
        except VoteRuleViolation as error:
            if error.code == "RESOLVER_LEASE_HELD":
                return TurnProcessingResult(due_turn, TurnProcessingStatus.RESOLVER_BUSY)
            raise

        history = await self._games.load_game(due_turn.game_id)
        if history is None:
            raise PersistenceRuleViolation("GAME_NOT_FOUND")
        resolution = await self._resolution(due_turn, leased.snapshot, history)
        if resolution.applied_move is not None:
            await self._persist_move(due_turn, leased.snapshot, resolution)

        if resolution.end_reason is not None:
            game = self._rebuild_before_turn(leased.snapshot, history)
            if resolution.result is TurnResultKind.JOINT_LOSS:
                game.finish_joint_loss()
            else:
                assert resolution.selected_coordinate is not None
                game.apply_move(
                    team=resolution.team,
                    coordinate=resolution.selected_coordinate,
                )
            result = GameResultService(
                game_id=due_turn.game_id,
                game=game,
                participants=history.participants,
            ).finalize_completed_game()
            ended_at = self._event_time(leased.snapshot)
            command = FinalizeGameCommand(result=result, ended_at=ended_at)
            if not await self._games.result_matches(command):
                await self._games.finalize_game(command)

        try:
            applied = await self._votes.apply_resolution(
                ApplyRuntimeResolution(
                    room_id=due_turn.room_id,
                    request_id=_stable_id("apply", due_turn),
                    game_id=due_turn.game_id,
                    turn_no=due_turn.turn_no,
                    resolution_id=self._resolution_id(due_turn),
                    resolution=resolution,
                    expected_state_version=leased.snapshot.state_version,
                    persistence_confirmed=True,
                    next_deadline_ms=(
                        None
                        if resolution.end_reason is not None
                        else self._next_deadline(leased.snapshot, history)
                    ),
                )
            )
        except VoteRuleViolation as error:
            if error.code in {"RESOLVER_LEASE_EXPIRED", "RESOLVER_NOT_OWNER"}:
                return TurnProcessingResult(
                    due_turn,
                    TurnProcessingStatus.RETRY_REQUIRED,
                    resolution,
                )
            raise

        if resolution.end_reason is None:
            return TurnProcessingResult(due_turn, TurnProcessingStatus.MOVE, applied.resolution)
        await self._complete_room(due_turn)
        return TurnProcessingResult(due_turn, TurnProcessingStatus.GAME_ENDED, applied.resolution)

    async def _resolution(
        self,
        due_turn: DueTurn,
        snapshot: VoteRuntimeSnapshot,
        history: GamePersistenceSnapshot,
    ) -> TurnResolution:
        if not snapshot.candidates:
            if snapshot.consecutive_passes != 1:
                raise VoteRuleViolation("RESOLUTION_CANDIDATES_MISSING")
            return TurnResolution(
                game_id=due_turn.game_id,
                turn_no=due_turn.turn_no,
                team=snapshot.current_team,
                result=TurnResultKind.JOINT_LOSS,
                status=TurnStatus.PASSED,
                selected_coordinate=None,
                applied_move=None,
                end_reason=EndReason.JOINT_LOSS,
            )

        existing = await self._games.get_move(due_turn.game_id, due_turn.turn_no)
        candidate_values = tuple(item.canonical for item in snapshot.candidates)
        selected_value: str | None
        if existing is not None:
            selected_value = existing.coordinate.canonical
        elif len(candidate_values) == 1:
            selected_value = None
        else:
            selected_value = await self._tie_selector.select(
                game_id=due_turn.game_id,
                turn_no=due_turn.turn_no,
                candidates=candidate_values,
            )
        selected = VoteTurnGame.select_candidate(snapshot.candidates, selected_value)
        if len(candidate_values) > 1:
            await self._tie_audit.record(
                TieSelectionRecord(
                    due_turn.game_id,
                    due_turn.turn_no,
                    candidate_values,
                    selected.canonical,
                )
            )
        game = self._rebuild_before_turn(snapshot, history)
        outcome = game.apply_move(team=snapshot.current_team, coordinate=selected)
        return TurnResolution(
            game_id=due_turn.game_id,
            turn_no=due_turn.turn_no,
            team=snapshot.current_team,
            result=TurnResultKind.MOVE_APPLIED,
            status=TurnStatus.MOVE_APPLIED,
            selected_coordinate=selected,
            applied_move=outcome.move,
            end_reason=outcome.end_reason,
        )

    async def _persist_move(
        self,
        due_turn: DueTurn,
        snapshot: VoteRuntimeSnapshot,
        resolution: TurnResolution,
    ) -> None:
        move = resolution.applied_move
        assert move is not None
        existing = await self._games.get_move(due_turn.game_id, due_turn.turn_no)
        final_vote_count = next(
            item.count for item in snapshot.tally if item.coordinate == move.coordinate
        )
        valid_voter_count = snapshot.valid_voter_count
        if valid_voter_count is None:
            raise VoteRuleViolation("VALID_VOTER_COUNT_MISSING")
        confirmed_at = existing.confirmed_at if existing is not None else self._event_time(snapshot)
        command = OfficialMoveRecord(
            game_id=due_turn.game_id,
            turn_no=due_turn.turn_no,
            move_no=move.move_no,
            team=move.team,
            coordinate=move.coordinate,
            final_vote_count=final_vote_count,
            valid_voter_count=valid_voter_count,
            confirmed_at=confirmed_at,
        )
        if existing is not None:
            if existing != command:
                raise PersistenceRuleViolation("MOVE_SEQUENCE_CONFLICT")
            return
        await self._games.append_move(command)

    def _rebuild_before_turn(
        self,
        snapshot: VoteRuntimeSnapshot,
        history: GamePersistenceSnapshot,
    ) -> Game:
        game = Game()
        next_turn = 1
        for move in history.moves:
            if move.turn_no >= snapshot.turn_no:
                break
            while next_turn < move.turn_no:
                game.pass_turn()
                next_turn += 1
            outcome = game.apply_move(team=move.team, coordinate=move.coordinate)
            if outcome.move.move_no != move.move_no:
                raise PersistenceRuleViolation("MOVE_SEQUENCE_CONFLICT")
            next_turn = move.turn_no + 1
        while next_turn < snapshot.turn_no:
            game.pass_turn()
            next_turn += 1
        if (
            game.current_team is not snapshot.current_team
            or game.move_no != snapshot.move_no
            or set(game.occupied_cells) != set(snapshot.occupied_cells)
        ):
            raise PersistenceRuleViolation("GAME_RUNTIME_HISTORY_MISMATCH")
        return game

    async def _require_playing_room(self, due_turn: DueTurn) -> RoomRuntimeSnapshot:
        room = await self._rooms.get(due_turn.room_id)
        if room is None:
            raise VoteRuleViolation("ROOM_NOT_FOUND")
        if room.status is not RoomStatus.PLAYING or room.game_id != due_turn.game_id:
            raise VoteRuleViolation("GAME_NOT_IN_CURRENT_ROOM")
        return room

    async def _complete_room(self, due_turn: DueTurn) -> None:
        room = await self._rooms.get(due_turn.room_id)
        if room is None:
            raise VoteRuleViolation("ROOM_NOT_FOUND")
        if room.status is RoomStatus.WAITING and room.game_id is None:
            return
        await self._rooms.complete_game(
            CompleteRoomGame(
                room_id=due_turn.room_id,
                request_id=_stable_id("room-complete", due_turn),
                game_id=due_turn.game_id,
                expected_state_version=room.state_version,
            )
        )

    def _next_deadline(
        self,
        snapshot: VoteRuntimeSnapshot,
        history: GamePersistenceSnapshot,
    ) -> int:
        if snapshot.deadline_ms is None:
            raise VoteRuleViolation("INVALID_NEXT_DEADLINE")
        return snapshot.deadline_ms + history.start.voting_time_seconds * 1000

    def _event_time(self, snapshot: VoteRuntimeSnapshot) -> datetime:
        timestamp_ms = snapshot.deadline_ms
        if timestamp_ms is None:
            timestamp_ms = self._clock.now_ms
        return datetime.fromtimestamp(timestamp_ms / 1000, UTC)

    def _resolution_id(self, due_turn: DueTurn) -> str:
        return _stable_id(f"resolution-{self._runner_id}", due_turn)


def _stable_id(prefix: str, due_turn: DueTurn) -> str:
    value = f"{due_turn.game_id}:{due_turn.turn_no}".encode()
    return f"{prefix}-{hashlib.sha256(value).hexdigest()[:24]}"
