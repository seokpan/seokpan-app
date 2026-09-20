import pytest

from seokpan.api.problems import _game_status, _room_status


@pytest.mark.parametrize(
    "code",
    [
        "GAME_CLOSURE_UNCONFIRMED",
        "GAME_START_RECOVERY_REQUIRED",
        "GAME_START_RESULT_INVALID",
        "GAME_START_TIME_MISSING",
        "PARTICIPANT_IDENTITY_NOT_FOUND",
        "START_CLOSURE_INVALID",
        "START_COMPLETION_INVALID",
    ],
)
def test_internal_room_recovery_failures_are_service_unavailable(code: str) -> None:
    status, title = _room_status(code)

    assert status == 503
    assert title == "Room state unavailable"


@pytest.mark.parametrize(
    "code",
    [
        "GAME_END_REASON_MISSING",
        "GAME_HISTORY_INVALID",
        "GAME_RESULT_INCOMPLETE",
        "GAME_RESULT_HISTORY_MISMATCH",
        "GAME_RUNTIME_HISTORY_MISMATCH",
        "GAME_START_RECOVERY_REQUIRED",
        "INVALID_NEXT_DEADLINE",
        "RESOLUTION_CANDIDATES_MISSING",
        "TURN_CLOSURE_MISSING",
        "VALID_VOTER_COUNT_MISSING",
    ],
)
def test_internal_game_recovery_failures_are_service_unavailable(code: str) -> None:
    status, title = _game_status(code)

    assert status == 503
    assert title == "Game state unavailable"


@pytest.mark.parametrize(
    ("mapper", "code"),
    [
        (_room_status, "STATE_VERSION_CONFLICT"),
        (_room_status, "ROOM_NOT_WAITING"),
        (_game_status, "STALE_GAME"),
        (_game_status, "TURN_NOT_VOTING"),
    ],
)
def test_user_visible_state_conflicts_remain_conflicts(mapper, code: str) -> None:
    status, _title = mapper(code)

    assert status == 409


@pytest.mark.parametrize(
    ("mapper", "code"),
    [
        (_room_status, "INVALID_REQUEST_ID"),
        (_room_status, "INVALID_STATE_VERSION"),
        (_game_status, "INVALID_COORDINATE"),
    ],
)
def test_request_validation_errors_remain_unprocessable(mapper, code: str) -> None:
    status, _title = mapper(code)

    assert status == 422
