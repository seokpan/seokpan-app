import pytest

from seokpan.game.domain import (
    Coordinate,
    EndReason,
    Game,
    GameRuleViolation,
    GameStatus,
    Stone,
    TurnRuleViolation,
    TurnStatus,
    VotingMatch,
    VotingParticipant,
)


def participants() -> tuple[VotingParticipant, ...]:
    return (
        VotingParticipant(participant_id="black-1", team=Stone.BLACK),
        VotingParticipant(participant_id="black-2", team=Stone.BLACK),
        VotingParticipant(participant_id="white-1", team=Stone.WHITE),
        VotingParticipant(participant_id="white-2", team=Stone.WHITE),
        VotingParticipant(
            participant_id="spectator",
            team=Stone.WHITE,
            is_player=False,
        ),
    )


def match() -> VotingMatch:
    return VotingMatch(game_id="game-1", participants=participants(), deadline_at=100.0)


def snapshot(voting_match: VotingMatch) -> tuple[object, ...]:
    return (
        voting_match.turn_no,
        voting_match.deadline_at,
        voting_match.status,
        voting_match.current_team,
        voting_match.consecutive_passes,
        voting_match.votes,
        voting_match.game.status,
        voting_match.game.current_team,
        voting_match.game.move_no,
        voting_match.game.end_reason,
        voting_match.game.moves,
        voting_match.game.occupied_cells,
    )


def assert_rejected_without_mutation(
    voting_match: VotingMatch,
    expected_code: str,
    command: object,
) -> None:
    before = snapshot(voting_match)
    with pytest.raises(TurnRuleViolation, match=expected_code) as error:
        assert callable(command)
        command()
    assert error.value.code == expected_code
    assert snapshot(voting_match) == before


def test_vote_create_replace_delete_and_disconnect_leave_only_last_valid_vote() -> None:
    voting_match = match()

    voting_match.cast_vote(
        participant_id="black-1",
        game_id="game-1",
        turn_no=1,
        coordinate="H8",
        now=90.0,
    )
    voting_match.cast_vote(
        participant_id="black-1",
        game_id="game-1",
        turn_no=1,
        coordinate="H9",
        now=91.0,
    )
    assert [(pid, coordinate.canonical) for pid, coordinate in voting_match.votes] == [
        ("black-1", "H9")
    ]

    voting_match.remove_vote(
        participant_id="black-1",
        game_id="game-1",
        turn_no=1,
        now=92.0,
    )
    assert voting_match.votes == ()

    voting_match.cast_vote(
        participant_id="black-2",
        game_id="game-1",
        turn_no=1,
        coordinate="G8",
        now=93.0,
    )
    voting_match.set_connected(participant_id="black-2", connected=False)
    assert voting_match.votes == ()

    voting_match.set_connected(participant_id="black-2", connected=False)
    voting_match.set_connected(participant_id="black-2", connected=True)
    assert voting_match.votes == ()


def test_vote_rejects_wrong_team_spectator_disconnected_deadline_and_stale_requests() -> None:
    voting_match = match()

    assert_rejected_without_mutation(
        voting_match,
        "CURRENT_TEAM_REQUIRED",
        lambda: voting_match.cast_vote(
            participant_id="white-1",
            game_id="game-1",
            turn_no=1,
            coordinate="H8",
            now=90.0,
        ),
    )
    assert_rejected_without_mutation(
        voting_match,
        "PLAYER_REQUIRED",
        lambda: voting_match.cast_vote(
            participant_id="spectator",
            game_id="game-1",
            turn_no=1,
            coordinate="H8",
            now=90.0,
        ),
    )
    voting_match.set_connected(participant_id="black-1", connected=False)
    assert_rejected_without_mutation(
        voting_match,
        "PARTICIPANT_DISCONNECTED",
        lambda: voting_match.cast_vote(
            participant_id="black-1",
            game_id="game-1",
            turn_no=1,
            coordinate="H8",
            now=90.0,
        ),
    )
    assert_rejected_without_mutation(
        voting_match,
        "VOTING_CLOSED",
        lambda: voting_match.cast_vote(
            participant_id="black-2",
            game_id="game-1",
            turn_no=1,
            coordinate="H8",
            now=100.0,
        ),
    )
    assert_rejected_without_mutation(
        voting_match,
        "STALE_GAME",
        lambda: voting_match.cast_vote(
            participant_id="black-2",
            game_id="old-game",
            turn_no=1,
            coordinate="H8",
            now=90.0,
        ),
    )
    assert_rejected_without_mutation(
        voting_match,
        "STALE_TURN",
        lambda: voting_match.cast_vote(
            participant_id="black-2",
            game_id="game-1",
            turn_no=2,
            coordinate="H8",
            now=90.0,
        ),
    )


def test_single_highest_vote_applies_existing_game_move_and_resets_pass_counter() -> None:
    voting_match = match()
    voting_match.cast_vote(
        participant_id="black-1",
        game_id="game-1",
        turn_no=1,
        coordinate="H8",
        now=90.0,
    )
    voting_match.cast_vote(
        participant_id="black-2",
        game_id="game-1",
        turn_no=1,
        coordinate="H8",
        now=91.0,
    )

    result = voting_match.close_turn(
        game_id="game-1",
        turn_no=1,
        now=100.0,
        next_deadline_at=200.0,
    )

    assert result.passed is False
    assert result.selected_coordinate is not None
    assert result.selected_coordinate.canonical == "H8"
    assert result.move_outcome is not None
    assert result.move_outcome.move.move_no == 1
    assert voting_match.game.stone_at("H8") is Stone.BLACK
    assert voting_match.current_team is Stone.WHITE
    assert voting_match.turn_no == 2
    assert voting_match.consecutive_passes == 0


def test_tie_requires_external_selection_and_rejects_non_candidate_without_mutation() -> None:
    voting_match = match()
    voting_match.cast_vote(
        participant_id="black-1",
        game_id="game-1",
        turn_no=1,
        coordinate="H8",
        now=90.0,
    )
    voting_match.cast_vote(
        participant_id="black-2",
        game_id="game-1",
        turn_no=1,
        coordinate="H9",
        now=91.0,
    )

    assert [item.coordinate.canonical for item in voting_match.tally()] == ["H8", "H9"]
    assert_rejected_without_mutation(
        voting_match,
        "TIE_SELECTION_REQUIRED",
        lambda: voting_match.close_turn(
            game_id="game-1",
            turn_no=1,
            now=100.0,
            next_deadline_at=200.0,
        ),
    )
    assert_rejected_without_mutation(
        voting_match,
        "INVALID_TIE_SELECTION",
        lambda: voting_match.close_turn(
            game_id="game-1",
            turn_no=1,
            now=100.0,
            next_deadline_at=200.0,
            tie_selection="H10",
        ),
    )
    assert_rejected_without_mutation(
        voting_match,
        "INVALID_COORDINATE",
        lambda: voting_match.close_turn(
            game_id="game-1",
            turn_no=1,
            now=100.0,
            next_deadline_at=200.0,
            tie_selection="P1",
        ),
    )

    result = voting_match.close_turn(
        game_id="game-1",
        turn_no=1,
        now=100.0,
        next_deadline_at=200.0,
        tie_selection=Coordinate.parse("H9"),
    )
    assert tuple(item.canonical for item in result.candidates) == ("H8", "H9")
    assert result.selected_coordinate is not None
    assert result.selected_coordinate.canonical == "H9"


def test_zero_vote_pass_keeps_move_number_and_two_consecutive_passes_end_both_lose() -> None:
    voting_match = match()

    first = voting_match.close_turn(
        game_id="game-1",
        turn_no=1,
        now=100.0,
        next_deadline_at=200.0,
    )
    assert first.passed is True
    assert first.consecutive_passes == 1
    assert first.game_status is GameStatus.ACTIVE
    assert voting_match.game.move_no == 0
    assert voting_match.current_team is Stone.WHITE

    second = voting_match.close_turn(
        game_id="game-1",
        turn_no=2,
        now=200.0,
        next_deadline_at=300.0,
    )
    assert second.passed is True
    assert second.consecutive_passes == 2
    assert second.game_status is GameStatus.FINISHED
    assert second.end_reason is EndReason.BOTH_LOSE
    assert voting_match.game.move_no == 0
    assert voting_match.status is TurnStatus.FINISHED
    assert voting_match.current_team is Stone.EMPTY


def test_move_after_pass_resets_consecutive_pass_count() -> None:
    voting_match = match()
    voting_match.close_turn(
        game_id="game-1",
        turn_no=1,
        now=100.0,
        next_deadline_at=200.0,
    )
    voting_match.cast_vote(
        participant_id="white-1",
        game_id="game-1",
        turn_no=2,
        coordinate="H8",
        now=190.0,
    )

    result = voting_match.close_turn(
        game_id="game-1",
        turn_no=2,
        now=200.0,
        next_deadline_at=300.0,
    )
    assert result.passed is False
    assert voting_match.consecutive_passes == 0
    assert voting_match.game.move_no == 1
    assert voting_match.current_team is Stone.BLACK


def test_same_close_request_is_idempotent_without_duplicate_move_or_pass() -> None:
    voting_match = match()
    voting_match.cast_vote(
        participant_id="black-1",
        game_id="game-1",
        turn_no=1,
        coordinate="H8",
        now=90.0,
    )
    first = voting_match.close_turn(
        game_id="game-1",
        turn_no=1,
        now=100.0,
        next_deadline_at=200.0,
    )
    second = voting_match.close_turn(
        game_id="game-1",
        turn_no=1,
        now=150.0,
        next_deadline_at=250.0,
    )

    assert second == first
    assert voting_match.game.move_no == 1
    assert voting_match.turn_no == 2


def test_invalid_next_deadline_and_close_before_deadline_do_not_mutate_state() -> None:
    voting_match = match()
    voting_match.cast_vote(
        participant_id="black-1",
        game_id="game-1",
        turn_no=1,
        coordinate="H8",
        now=90.0,
    )

    assert_rejected_without_mutation(
        voting_match,
        "TURN_DEADLINE_NOT_REACHED",
        lambda: voting_match.close_turn(
            game_id="game-1",
            turn_no=1,
            now=99.0,
            next_deadline_at=200.0,
        ),
    )
    assert_rejected_without_mutation(
        voting_match,
        "INVALID_NEXT_DEADLINE",
        lambda: voting_match.close_turn(
            game_id="game-1",
            turn_no=1,
            now=100.0,
            next_deadline_at=100.0,
        ),
    )


def test_participant_and_match_construction_reject_invalid_inputs() -> None:
    with pytest.raises(TurnRuleViolation, match="INVALID_PARTICIPANT_ID"):
        VotingParticipant(participant_id="", team=Stone.BLACK)
    with pytest.raises(TurnRuleViolation, match="INVALID_PARTICIPANT_TEAM"):
        VotingParticipant(participant_id="empty-team", team=Stone.EMPTY)
    with pytest.raises(TurnRuleViolation, match="INVALID_GAME_ID"):
        VotingMatch(game_id="", participants=participants(), deadline_at=100.0)
    with pytest.raises(TurnRuleViolation, match="INVALID_DEADLINE"):
        VotingMatch(game_id="game-1", participants=participants(), deadline_at=0.0)

    duplicate = (
        VotingParticipant(participant_id="same", team=Stone.BLACK),
        VotingParticipant(participant_id="same", team=Stone.WHITE),
    )
    with pytest.raises(TurnRuleViolation, match="DUPLICATE_PARTICIPANT_ID"):
        VotingMatch(game_id="game-1", participants=duplicate, deadline_at=100.0)


def test_unknown_participant_is_rejected_for_connection_vote_and_remove() -> None:
    voting_match = match()
    with pytest.raises(TurnRuleViolation, match="PARTICIPANT_NOT_FOUND"):
        voting_match.set_connected(participant_id="missing", connected=False)

    assert_rejected_without_mutation(
        voting_match,
        "PARTICIPANT_NOT_FOUND",
        lambda: voting_match.cast_vote(
            participant_id="missing",
            game_id="game-1",
            turn_no=1,
            coordinate="H8",
            now=90.0,
        ),
    )
    assert_rejected_without_mutation(
        voting_match,
        "PARTICIPANT_NOT_FOUND",
        lambda: voting_match.remove_vote(
            participant_id="missing",
            game_id="game-1",
            turn_no=1,
            now=90.0,
        ),
    )


def test_vote_candidate_validation_reuses_board_occupied_invalid_and_black_forbidden_rules() -> None:
    voting_match = match()
    assert_rejected_without_mutation(
        voting_match,
        "INVALID_COORDINATE",
        lambda: voting_match.cast_vote(
            participant_id="black-1",
            game_id="game-1",
            turn_no=1,
            coordinate="P1",
            now=90.0,
        ),
    )

    game = Game()
    game.apply_move(team=Stone.BLACK, coordinate="H8")
    occupied_match = VotingMatch(
        game_id="game-2",
        participants=participants(),
        deadline_at=100.0,
        game=game,
    )
    assert_rejected_without_mutation(
        occupied_match,
        "POSITION_OCCUPIED",
        lambda: occupied_match.cast_vote(
            participant_id="white-1",
            game_id="game-2",
            turn_no=1,
            coordinate=Coordinate.parse("H8"),
            now=90.0,
        ),
    )

    forbidden_game = Game()
    for black, white in zip(
        ("G8", "I8", "H7", "H9"),
        ("A1", "C1", "E1", "G1"),
        strict=True,
    ):
        forbidden_game.apply_move(team=Stone.BLACK, coordinate=black)
        forbidden_game.apply_move(team=Stone.WHITE, coordinate=white)
    forbidden_match = VotingMatch(
        game_id="game-3",
        participants=participants(),
        deadline_at=100.0,
        game=forbidden_game,
    )
    assert_rejected_without_mutation(
        forbidden_match,
        "BLACK_DOUBLE_THREE",
        lambda: forbidden_match.cast_vote(
            participant_id="black-1",
            game_id="game-3",
            turn_no=1,
            coordinate="H8",
            now=90.0,
        ),
    )


def test_close_rejects_stale_game_stale_turn_and_non_voting_game_without_mutation() -> None:
    voting_match = match()
    assert_rejected_without_mutation(
        voting_match,
        "STALE_GAME",
        lambda: voting_match.close_turn(
            game_id="old-game",
            turn_no=1,
            now=100.0,
            next_deadline_at=200.0,
        ),
    )
    assert_rejected_without_mutation(
        voting_match,
        "STALE_TURN",
        lambda: voting_match.close_turn(
            game_id="game-1",
            turn_no=2,
            now=100.0,
            next_deadline_at=200.0,
        ),
    )

    finished_game = Game()
    for black, white in zip(
        ("E8", "F8", "G8", "H8"),
        ("A1", "C1", "E1", "G1"),
        strict=True,
    ):
        finished_game.apply_move(team=Stone.BLACK, coordinate=black)
        finished_game.apply_move(team=Stone.WHITE, coordinate=white)
    finished_game.apply_move(team=Stone.BLACK, coordinate="I8")
    finished_match = VotingMatch(
        game_id="finished",
        participants=participants(),
        deadline_at=100.0,
        game=finished_game,
    )
    assert_rejected_without_mutation(
        finished_match,
        "TURN_NOT_VOTING",
        lambda: finished_match.close_turn(
            game_id="finished",
            turn_no=1,
            now=100.0,
            next_deadline_at=200.0,
        ),
    )
    assert_rejected_without_mutation(
        finished_match,
        "TURN_NOT_VOTING",
        lambda: finished_match.cast_vote(
            participant_id="black-1",
            game_id="finished",
            turn_no=1,
            coordinate="J8",
            now=90.0,
        ),
    )


def test_close_propagates_existing_game_rejection_without_mutating_vote_state() -> None:
    game = Game()
    voting_match = VotingMatch(
        game_id="game-race",
        participants=participants(),
        deadline_at=100.0,
        game=game,
    )
    voting_match.cast_vote(
        participant_id="black-1",
        game_id="game-race",
        turn_no=1,
        coordinate="H8",
        now=90.0,
    )
    game.apply_move(team=Stone.BLACK, coordinate="H8")

    before_votes = voting_match.votes
    with pytest.raises(TurnRuleViolation, match="POSITION_OCCUPIED") as error:
        voting_match.close_turn(
            game_id="game-race",
            turn_no=1,
            now=100.0,
            next_deadline_at=200.0,
        )
    assert error.value.code == "POSITION_OCCUPIED"
    assert voting_match.votes == before_votes
    assert voting_match.turn_no == 1


def test_single_candidate_optional_tie_selection_is_validated() -> None:
    voting_match = match()
    voting_match.cast_vote(
        participant_id="black-1",
        game_id="game-1",
        turn_no=1,
        coordinate="H8",
        now=90.0,
    )
    assert_rejected_without_mutation(
        voting_match,
        "INVALID_TIE_SELECTION",
        lambda: voting_match.close_turn(
            game_id="game-1",
            turn_no=1,
            now=100.0,
            next_deadline_at=200.0,
            tie_selection=Coordinate.parse("H9"),
        ),
    )
    assert_rejected_without_mutation(
        voting_match,
        "INVALID_COORDINATE",
        lambda: voting_match.close_turn(
            game_id="game-1",
            turn_no=1,
            now=100.0,
            next_deadline_at=200.0,
            tie_selection="P1",
        ),
    )

    result = voting_match.close_turn(
        game_id="game-1",
        turn_no=1,
        now=100.0,
        next_deadline_at=200.0,
        tie_selection=Coordinate.parse("H8"),
    )
    assert result.selected_coordinate == Coordinate.parse("H8")


def test_game_apply_pass_rejects_invalid_state_team_turn_and_count_without_mutation() -> None:
    game = Game()
    with pytest.raises(GameRuleViolation, match="INVALID_MOVE_TEAM"):
        game.apply_pass(team=Stone.EMPTY, consecutive_passes=1)
    with pytest.raises(GameRuleViolation, match="NOT_CURRENT_TEAM"):
        game.apply_pass(team=Stone.WHITE, consecutive_passes=1)
    with pytest.raises(GameRuleViolation, match="INVALID_CONSECUTIVE_PASSES"):
        game.apply_pass(team=Stone.BLACK, consecutive_passes=0)

    first = game.apply_pass(team=Stone.BLACK, consecutive_passes=1)
    assert first.status is GameStatus.ACTIVE
    second = game.apply_pass(team=Stone.WHITE, consecutive_passes=2)
    assert second.status is GameStatus.FINISHED
    assert second.end_reason is EndReason.BOTH_LOSE

    with pytest.raises(GameRuleViolation, match="GAME_NOT_ACTIVE"):
        game.apply_pass(team=Stone.BLACK, consecutive_passes=3)
