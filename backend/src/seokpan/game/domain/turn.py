"""Vote and Turn pure domain rules for the MVP game flow."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from seokpan.game.domain.model import (
    Coordinate,
    EndReason,
    Game,
    GameRuleViolation,
    GameStatus,
    MoveOutcome,
    Stone,
)


class TurnRuleViolation(ValueError):
    """A stable rejection which must not mutate Vote/Turn state."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TurnStatus(StrEnum):
    VOTING = "VOTING"
    FINISHED = "FINISHED"


@dataclass(frozen=True, slots=True)
class VotingParticipant:
    participant_id: str
    team: Stone
    is_player: bool = True
    connected: bool = True

    def __post_init__(self) -> None:
        if not self.participant_id:
            raise TurnRuleViolation("INVALID_PARTICIPANT_ID")
        if self.team is Stone.EMPTY:
            raise TurnRuleViolation("INVALID_PARTICIPANT_TEAM")


@dataclass(frozen=True, slots=True)
class VoteTally:
    coordinate: Coordinate
    votes: int


@dataclass(frozen=True, slots=True)
class TurnCloseResult:
    turn_no: int
    team: Stone
    tallies: tuple[VoteTally, ...]
    candidates: tuple[Coordinate, ...]
    selected_coordinate: Coordinate | None
    move_outcome: MoveOutcome | None
    passed: bool
    consecutive_passes: int
    game_status: GameStatus
    end_reason: EndReason | None
    next_team: Stone


class VotingMatch:
    """Vote/Turn authority that composes the existing Board Game domain."""

    def __init__(
        self,
        *,
        game_id: str,
        participants: tuple[VotingParticipant, ...],
        deadline_at: float,
        game: Game | None = None,
    ) -> None:
        if not game_id:
            raise TurnRuleViolation("INVALID_GAME_ID")
        if deadline_at <= 0:
            raise TurnRuleViolation("INVALID_DEADLINE")
        participant_map = {item.participant_id: item for item in participants}
        if len(participant_map) != len(participants):
            raise TurnRuleViolation("DUPLICATE_PARTICIPANT_ID")

        self.game_id = game_id
        self.game = game or Game()
        self.turn_no = 1
        self.deadline_at = deadline_at
        self.status = TurnStatus.VOTING
        self._participants = participant_map
        self._votes: dict[str, Coordinate] = {}
        self._consecutive_passes = 0
        self._closed_results: dict[int, TurnCloseResult] = {}

    @property
    def current_team(self) -> Stone:
        return self.game.current_team

    @property
    def votes(self) -> tuple[tuple[str, Coordinate], ...]:
        return tuple(sorted(self._votes.items()))

    @property
    def consecutive_passes(self) -> int:
        return self._consecutive_passes

    def cast_vote(
        self,
        *,
        participant_id: str,
        game_id: str,
        turn_no: int,
        coordinate: Coordinate | str,
        now: float,
    ) -> None:
        self._require_current_request(game_id=game_id, turn_no=turn_no)
        self._require_voting_open(now=now)
        participant = self._eligible_participant(participant_id)
        parsed = self._validate_candidate(coordinate)
        self._votes[participant.participant_id] = parsed

    def remove_vote(
        self,
        *,
        participant_id: str,
        game_id: str,
        turn_no: int,
        now: float,
    ) -> None:
        self._require_current_request(game_id=game_id, turn_no=turn_no)
        self._require_voting_open(now=now)
        self._eligible_participant(participant_id)
        self._votes.pop(participant_id, None)

    def set_connected(self, *, participant_id: str, connected: bool) -> None:
        try:
            participant = self._participants[participant_id]
        except KeyError as error:
            raise TurnRuleViolation("PARTICIPANT_NOT_FOUND") from error
        if participant.connected is connected:
            return
        self._participants[participant_id] = VotingParticipant(
            participant_id=participant.participant_id,
            team=participant.team,
            is_player=participant.is_player,
            connected=connected,
        )
        if not connected:
            self._votes.pop(participant_id, None)

    def tally(self) -> tuple[VoteTally, ...]:
        counts = Counter(self._votes.values())
        return tuple(
            VoteTally(coordinate=coordinate, votes=vote_count)
            for coordinate, vote_count in sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0].row, item[0].column),
            )
        )

    def close_turn(
        self,
        *,
        game_id: str,
        turn_no: int,
        now: float,
        next_deadline_at: float,
        tie_selection: Coordinate | str | None = None,
    ) -> TurnCloseResult:
        if game_id != self.game_id:
            raise TurnRuleViolation("STALE_GAME")
        previous = self._closed_results.get(turn_no)
        if previous is not None:
            return previous
        if turn_no != self.turn_no:
            raise TurnRuleViolation("STALE_TURN")
        if self.status is not TurnStatus.VOTING or self.game.status is not GameStatus.ACTIVE:
            raise TurnRuleViolation("TURN_NOT_VOTING")
        if now < self.deadline_at:
            raise TurnRuleViolation("TURN_DEADLINE_NOT_REACHED")
        if next_deadline_at <= self.deadline_at:
            raise TurnRuleViolation("INVALID_NEXT_DEADLINE")

        team = self.current_team
        tallies = self.tally()
        candidates = self._highest_candidates(tallies)

        if not candidates:
            next_consecutive_passes = self._consecutive_passes + 1
            self.game.apply_pass(team=team, consecutive_passes=next_consecutive_passes)
            self._consecutive_passes = next_consecutive_passes
            result = TurnCloseResult(
                turn_no=turn_no,
                team=team,
                tallies=(),
                candidates=(),
                selected_coordinate=None,
                move_outcome=None,
                passed=True,
                consecutive_passes=self._consecutive_passes,
                game_status=self.game.status,
                end_reason=self.game.end_reason,
                next_team=self.game.current_team,
            )
            self._finish_turn(result=result, next_deadline_at=next_deadline_at)
            return result

        selected = self._select_candidate(candidates=candidates, tie_selection=tie_selection)
        try:
            move_outcome = self.game.apply_move(team=team, coordinate=selected)
        except GameRuleViolation as error:
            raise TurnRuleViolation(error.code) from error

        self._consecutive_passes = 0
        result = TurnCloseResult(
            turn_no=turn_no,
            team=team,
            tallies=tallies,
            candidates=candidates,
            selected_coordinate=selected,
            move_outcome=move_outcome,
            passed=False,
            consecutive_passes=0,
            game_status=self.game.status,
            end_reason=self.game.end_reason,
            next_team=self.game.current_team,
        )
        self._finish_turn(result=result, next_deadline_at=next_deadline_at)
        return result

    def _finish_turn(self, *, result: TurnCloseResult, next_deadline_at: float) -> None:
        self._closed_results[result.turn_no] = result
        self._votes.clear()
        if self.game.status is GameStatus.FINISHED:
            self.status = TurnStatus.FINISHED
            return
        self.turn_no += 1
        self.deadline_at = next_deadline_at

    def _eligible_participant(self, participant_id: str) -> VotingParticipant:
        try:
            participant = self._participants[participant_id]
        except KeyError as error:
            raise TurnRuleViolation("PARTICIPANT_NOT_FOUND") from error
        if not participant.is_player:
            raise TurnRuleViolation("PLAYER_REQUIRED")
        if not participant.connected:
            raise TurnRuleViolation("PARTICIPANT_DISCONNECTED")
        if participant.team is not self.current_team:
            raise TurnRuleViolation("CURRENT_TEAM_REQUIRED")
        return participant

    def _validate_candidate(self, coordinate: Coordinate | str) -> Coordinate:
        try:
            parsed = (
                coordinate if isinstance(coordinate, Coordinate) else Coordinate.parse(coordinate)
            )
            if self.game.stone_at(parsed) is not Stone.EMPTY:
                raise TurnRuleViolation("POSITION_OCCUPIED")
            if self.current_team is Stone.BLACK:
                reason = self.game.black_forbidden_reason(parsed)
                if reason is not None:
                    raise TurnRuleViolation(f"BLACK_{reason.value}")
            return parsed
        except GameRuleViolation as error:
            raise TurnRuleViolation(error.code) from error

    @staticmethod
    def _highest_candidates(tallies: tuple[VoteTally, ...]) -> tuple[Coordinate, ...]:
        if not tallies:
            return ()
        highest = tallies[0].votes
        return tuple(item.coordinate for item in tallies if item.votes == highest)

    @staticmethod
    def _select_candidate(
        *,
        candidates: tuple[Coordinate, ...],
        tie_selection: Coordinate | str | None,
    ) -> Coordinate:
        if len(candidates) == 1:
            if tie_selection is not None:
                try:
                    parsed = (
                        tie_selection
                        if isinstance(tie_selection, Coordinate)
                        else Coordinate.parse(tie_selection)
                    )
                except GameRuleViolation as error:
                    raise TurnRuleViolation(error.code) from error
                if parsed != candidates[0]:
                    raise TurnRuleViolation("INVALID_TIE_SELECTION")
            return candidates[0]

        if tie_selection is None:
            raise TurnRuleViolation("TIE_SELECTION_REQUIRED")
        try:
            parsed = (
                tie_selection
                if isinstance(tie_selection, Coordinate)
                else Coordinate.parse(tie_selection)
            )
        except GameRuleViolation as error:
            raise TurnRuleViolation(error.code) from error
        if parsed not in candidates:
            raise TurnRuleViolation("INVALID_TIE_SELECTION")
        return parsed

    def _require_current_request(self, *, game_id: str, turn_no: int) -> None:
        if game_id != self.game_id:
            raise TurnRuleViolation("STALE_GAME")
        if turn_no != self.turn_no:
            raise TurnRuleViolation("STALE_TURN")
        if self.status is not TurnStatus.VOTING or self.game.status is not GameStatus.ACTIVE:
            raise TurnRuleViolation("TURN_NOT_VOTING")

    def _require_voting_open(self, *, now: float) -> None:
        if now >= self.deadline_at:
            raise TurnRuleViolation("VOTING_CLOSED")
