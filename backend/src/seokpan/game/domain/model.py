"""15x15 Board and MVP Renju rules without framework dependencies."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

BOARD_SIZE = 15
_COORDINATE_PATTERN = re.compile(r"([A-O])(1[0-5]|[1-9])")
_DIRECTIONS = ((1, 0), (0, 1), (1, 1), (1, -1))


class Stone(StrEnum):
    EMPTY = "EMPTY"
    BLACK = "BLACK"
    WHITE = "WHITE"


class GameStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FINISHED = "FINISHED"


class EndReason(StrEnum):
    BLACK_WIN = "BLACK_WIN"
    WHITE_WIN = "WHITE_WIN"
    DRAW = "DRAW"


class ForbiddenReason(StrEnum):
    DOUBLE_THREE = "DOUBLE_THREE"
    DOUBLE_FOUR = "DOUBLE_FOUR"
    OVERLINE = "OVERLINE"


class GameRuleViolation(ValueError):
    """A stable domain rejection which must not mutate Game state."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, order=True)
class Coordinate:
    column: int
    row: int

    def __post_init__(self) -> None:
        if not 1 <= self.column <= BOARD_SIZE or not 1 <= self.row <= BOARD_SIZE:
            raise GameRuleViolation("INVALID_COORDINATE")

    @classmethod
    def parse(cls, value: str) -> Coordinate:
        normalized = value.strip().upper()
        match = _COORDINATE_PATTERN.fullmatch(normalized)
        if match is None:
            raise GameRuleViolation("INVALID_COORDINATE")
        return cls(column=ord(match.group(1)) - ord("A") + 1, row=int(match.group(2)))

    @property
    def canonical(self) -> str:
        return f"{chr(ord('A') + self.column - 1)}{self.row}"


@dataclass(frozen=True, slots=True)
class BoardCell:
    coordinate: Coordinate
    stone: Stone


@dataclass(frozen=True, slots=True)
class AppliedMove:
    move_no: int
    team: Stone
    coordinate: Coordinate


@dataclass(frozen=True, slots=True)
class MoveOutcome:
    move: AppliedMove
    status: GameStatus
    end_reason: EndReason | None
    current_team: Stone
    winning_line: tuple[Coordinate, ...]


class Game:
    """Server-authoritative Board, Move sequence and Renju adjudication."""

    def __init__(self) -> None:
        self._board: dict[Coordinate, Stone] = {}
        self._moves: list[AppliedMove] = []
        self._status = GameStatus.ACTIVE
        self._current_team = Stone.BLACK
        self._end_reason: EndReason | None = None
        self._winning_line: tuple[Coordinate, ...] = ()

    @property
    def status(self) -> GameStatus:
        return self._status

    @property
    def current_team(self) -> Stone:
        return self._current_team

    @property
    def move_no(self) -> int:
        return len(self._moves)

    @property
    def end_reason(self) -> EndReason | None:
        return self._end_reason

    @property
    def winning_line(self) -> tuple[Coordinate, ...]:
        return self._winning_line

    @property
    def moves(self) -> tuple[AppliedMove, ...]:
        return tuple(self._moves)

    @property
    def occupied_cells(self) -> tuple[BoardCell, ...]:
        return tuple(
            BoardCell(coordinate=coordinate, stone=stone)
            for coordinate, stone in sorted(
                self._board.items(),
                key=lambda item: (item[0].row, item[0].column),
            )
        )

    def stone_at(self, coordinate: Coordinate | str) -> Stone:
        parsed = self._coordinate(coordinate)
        return self._board.get(parsed, Stone.EMPTY)

    def black_forbidden_reason(
        self,
        coordinate: Coordinate | str,
    ) -> ForbiddenReason | None:
        parsed = self._coordinate(coordinate)
        if parsed in self._board:
            raise GameRuleViolation("POSITION_OCCUPIED")
        proposed = dict(self._board)
        proposed[parsed] = Stone.BLACK
        return self._black_forbidden_reason(proposed, parsed)

    def apply_move(self, *, team: Stone, coordinate: Coordinate | str) -> MoveOutcome:
        if self._status is not GameStatus.ACTIVE:
            raise GameRuleViolation("GAME_NOT_ACTIVE")
        if team is Stone.EMPTY:
            raise GameRuleViolation("INVALID_MOVE_TEAM")
        if team is not self._current_team:
            raise GameRuleViolation("NOT_CURRENT_TEAM")

        parsed = self._coordinate(coordinate)
        if parsed in self._board:
            raise GameRuleViolation("POSITION_OCCUPIED")

        proposed = dict(self._board)
        proposed[parsed] = team
        if team is Stone.BLACK:
            forbidden_reason = self._black_forbidden_reason(proposed, parsed)
            if forbidden_reason is not None:
                raise GameRuleViolation(f"BLACK_{forbidden_reason.value}")

        move = AppliedMove(move_no=self.move_no + 1, team=team, coordinate=parsed)
        winning_line = self._winning_line_after(proposed, parsed, team)
        self._board = proposed
        self._moves.append(move)

        if winning_line:
            self._finish(
                reason=(EndReason.BLACK_WIN if team is Stone.BLACK else EndReason.WHITE_WIN),
                winning_line=winning_line,
            )
        elif len(self._board) == BOARD_SIZE * BOARD_SIZE:
            self._finish(reason=EndReason.DRAW, winning_line=())
        else:
            self._current_team = Stone.WHITE if team is Stone.BLACK else Stone.BLACK

        return MoveOutcome(
            move=move,
            status=self._status,
            end_reason=self._end_reason,
            current_team=self._current_team,
            winning_line=self._winning_line,
        )

    @staticmethod
    def _coordinate(coordinate: Coordinate | str) -> Coordinate:
        if isinstance(coordinate, Coordinate):
            return coordinate
        return Coordinate.parse(coordinate)

    def _finish(
        self,
        *,
        reason: EndReason,
        winning_line: tuple[Coordinate, ...],
    ) -> None:
        self._status = GameStatus.FINISHED
        self._end_reason = reason
        self._winning_line = winning_line
        self._current_team = Stone.EMPTY

    @classmethod
    def _black_forbidden_reason(
        cls,
        board: dict[Coordinate, Stone],
        coordinate: Coordinate,
    ) -> ForbiddenReason | None:
        if any(
            len(cls._contiguous_line(board, coordinate, Stone.BLACK, direction)) >= 6
            for direction in _DIRECTIONS
        ):
            return ForbiddenReason.OVERLINE

        four_directions = sum(
            cls._direction_has_four(board, coordinate, direction) for direction in _DIRECTIONS
        )
        if four_directions >= 2:
            return ForbiddenReason.DOUBLE_FOUR

        open_three_directions = sum(
            cls._direction_has_open_three(board, coordinate, direction) for direction in _DIRECTIONS
        )
        if open_three_directions >= 2:
            return ForbiddenReason.DOUBLE_THREE
        return None

    @classmethod
    def _direction_has_four(
        cls,
        board: dict[Coordinate, Stone],
        anchor: Coordinate,
        direction: tuple[int, int],
    ) -> bool:
        for extension in cls._line_coordinates(anchor, direction):
            if board.get(extension, Stone.EMPTY) is not Stone.EMPTY:
                continue
            future = dict(board)
            future[extension] = Stone.BLACK
            line = cls._contiguous_line(future, extension, Stone.BLACK, direction)
            if len(line) == 5 and anchor in line:
                return True
        return False

    @classmethod
    def _direction_has_open_three(
        cls,
        board: dict[Coordinate, Stone],
        anchor: Coordinate,
        direction: tuple[int, int],
    ) -> bool:
        for extension in cls._line_coordinates(anchor, direction):
            if board.get(extension, Stone.EMPTY) is not Stone.EMPTY:
                continue
            future = dict(board)
            future[extension] = Stone.BLACK
            if cls._has_straight_four(future, anchor, extension, direction):
                return True
        return False

    @classmethod
    def _has_straight_four(
        cls,
        board: dict[Coordinate, Stone],
        anchor: Coordinate,
        extension: Coordinate,
        direction: tuple[int, int],
    ) -> bool:
        coordinates = cls._line_coordinates(anchor, direction)
        for start in range(len(coordinates) - 5):
            window = coordinates[start : start + 6]
            if anchor not in window or extension not in window:
                continue
            stones = tuple(board.get(item, Stone.EMPTY) for item in window)
            if stones == (
                Stone.EMPTY,
                Stone.BLACK,
                Stone.BLACK,
                Stone.BLACK,
                Stone.BLACK,
                Stone.EMPTY,
            ):
                return True
        return False

    @classmethod
    def _winning_line_after(
        cls,
        board: dict[Coordinate, Stone],
        coordinate: Coordinate,
        team: Stone,
    ) -> tuple[Coordinate, ...]:
        for direction in _DIRECTIONS:
            line = cls._contiguous_line(board, coordinate, team, direction)
            if team is Stone.BLACK and len(line) == 5:
                return line
            if team is Stone.WHITE and len(line) >= 5:
                return line
        return ()

    @staticmethod
    def _contiguous_line(
        board: dict[Coordinate, Stone],
        coordinate: Coordinate,
        team: Stone,
        direction: tuple[int, int],
    ) -> tuple[Coordinate, ...]:
        delta_column, delta_row = direction
        before: list[Coordinate] = []
        cursor_column = coordinate.column - delta_column
        cursor_row = coordinate.row - delta_row
        while 1 <= cursor_column <= BOARD_SIZE and 1 <= cursor_row <= BOARD_SIZE:
            cursor = Coordinate(column=cursor_column, row=cursor_row)
            if board.get(cursor, Stone.EMPTY) is not team:
                break
            before.append(cursor)
            cursor_column -= delta_column
            cursor_row -= delta_row

        after: list[Coordinate] = []
        cursor_column = coordinate.column + delta_column
        cursor_row = coordinate.row + delta_row
        while 1 <= cursor_column <= BOARD_SIZE and 1 <= cursor_row <= BOARD_SIZE:
            cursor = Coordinate(column=cursor_column, row=cursor_row)
            if board.get(cursor, Stone.EMPTY) is not team:
                break
            after.append(cursor)
            cursor_column += delta_column
            cursor_row += delta_row

        return tuple(reversed(before)) + (coordinate,) + tuple(after)

    @staticmethod
    def _line_coordinates(
        coordinate: Coordinate,
        direction: tuple[int, int],
    ) -> tuple[Coordinate, ...]:
        delta_column, delta_row = direction
        column = coordinate.column
        row = coordinate.row
        while 1 <= column - delta_column <= BOARD_SIZE and 1 <= row - delta_row <= BOARD_SIZE:
            column -= delta_column
            row -= delta_row

        result: list[Coordinate] = []
        while 1 <= column <= BOARD_SIZE and 1 <= row <= BOARD_SIZE:
            result.append(Coordinate(column=column, row=row))
            column += delta_column
            row += delta_row
        return tuple(result)
