from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from seokpan.app import build_headless_services, create_app
from seokpan.game.application import DueTurn, PersistenceRuleViolation, TurnProcessingStatus
from seokpan.settings import Settings

ORIGIN = "http://localhost:5173"


@pytest.fixture
def application() -> FastAPI:
    settings = Settings(environment="test", allowed_origins=(ORIGIN,))
    return create_app(settings=settings, services=build_headless_services(settings))


def _member(client: TestClient, suffix: str) -> str:
    assert (
        client.post(
            "/api/v1/members",
            headers={"Origin": ORIGIN},
            json={
                "login_id": f"game_member_{suffix}",
                "nickname": f"게임회원{suffix}",
                "password": "correct-pass",
            },
        ).status_code
        == 201
    )
    login = client.post(
        "/api/v1/sessions/member",
        headers={"Origin": ORIGIN},
        json={"login_id": f"game_member_{suffix}", "password": "correct-pass"},
    )
    assert login.status_code == 200
    return str(login.json()["csrf_token"])


def _guest(client: TestClient) -> str:
    response = client.post("/api/v1/sessions/guest", headers={"Origin": ORIGIN})
    assert response.status_code == 201
    return str(response.json()["csrf_token"])


def _mutation(
    client: TestClient,
    csrf: str,
    method: str,
    path: str,
    version: int,
    **values: object,
) -> dict[str, object]:
    response = client.request(
        method,
        path,
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={
            "request_id": str(uuid4()),
            "expected_state_version": version,
            **values,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _ready_room(
    owner: TestClient,
    owner_csrf: str,
    white: TestClient,
    white_csrf: str,
) -> dict[str, object]:
    created = owner.post(
        "/api/v1/rooms",
        headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
        json={
            "request_id": str(uuid4()),
            "name": "Game HTTP 검증방",
            "minimum_ready": 2,
            "vote_seconds": 15,
        },
    )
    assert created.status_code == 201
    room = created.json()
    joined = white.post(
        f"/api/v1/rooms/{room['room_id']}/joins",
        headers={"Origin": ORIGIN, "X-CSRF-Token": white_csrf},
        json={
            "request_id": str(uuid4()),
            "expected_state_version": room["state_version"],
        },
    )
    assert joined.status_code == 201
    room = joined.json()
    room = _mutation(
        owner,
        owner_csrf,
        "PUT",
        f"/api/v1/rooms/{room['room_id']}/participants/me/team",
        int(room["state_version"]),
        team="BLACK",
    )
    room = _mutation(
        owner,
        owner_csrf,
        "PUT",
        f"/api/v1/rooms/{room['room_id']}/participants/me/ready",
        int(room["state_version"]),
        ready=True,
    )
    room = _mutation(
        white,
        white_csrf,
        "PUT",
        f"/api/v1/rooms/{room['room_id']}/participants/me/team",
        int(room["state_version"]),
        team="WHITE",
    )
    return _mutation(
        white,
        white_csrf,
        "PUT",
        f"/api/v1/rooms/{room['room_id']}/participants/me/ready",
        int(room["state_version"]),
        ready=True,
    )


def test_start_get_and_vote_headless_flow(application: FastAPI) -> None:
    with (
        TestClient(application, base_url=ORIGIN) as owner,
        TestClient(application, base_url=ORIGIN) as white,
    ):
        owner_csrf = _member(owner, "01")
        white_csrf = _member(white, "02")
        room = _ready_room(owner, owner_csrf, white, white_csrf)
        start_request = str(uuid4())
        started = owner.post(
            f"/api/v1/rooms/{room['room_id']}/games",
            headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
            json={
                "request_id": start_request,
                "expected_state_version": room["state_version"],
            },
        )
        assert started.status_code == 201, started.text
        game = started.json()
        replay = owner.post(
            f"/api/v1/rooms/{room['room_id']}/games",
            headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
            json={
                "request_id": start_request,
                "expected_state_version": room["state_version"],
            },
        )
        white_view = white.get(f"/api/v1/games/{game['game_id']}")
        vote = owner.put(
            f"/api/v1/games/{game['game_id']}/turns/1/vote",
            headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
            json={
                "request_id": str(uuid4()),
                "expected_state_version": game["state_version"],
                "coordinate": "H8",
            },
        )
        assert vote.status_code == 200, vote.text
        voted = vote.json()
        public_after_vote = white.get(f"/api/v1/games/{game['game_id']}").json()
        replaced = owner.put(
            f"/api/v1/games/{game['game_id']}/turns/1/vote",
            headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
            json={
                "request_id": str(uuid4()),
                "expected_state_version": voted["state_version"],
                "coordinate": "I8",
            },
        ).json()
        removed = owner.request(
            "DELETE",
            f"/api/v1/games/{game['game_id']}/turns/1/vote",
            headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
            json={
                "request_id": str(uuid4()),
                "expected_state_version": replaced["state_version"],
            },
        )

    assert UUID(game["game_id"]).version == 4
    assert game["game_status"] == "ACTIVE"
    assert game["turn_status"] == "VOTING"
    assert game["turn_no"] == 1
    assert game["move_no"] == 0
    assert game["current_team"] == "BLACK"
    assert game["can_vote"] is True
    assert len(game["participants"]) == 2
    assert all(item["role"] == "PLAYER" for item in game["participants"])
    assert replay.status_code == 201
    assert replay.json()["game_id"] == game["game_id"]
    assert replay.json()["replayed"] is True
    assert white_view.status_code == 200
    assert white_view.json()["can_vote"] is False
    assert voted["my_vote"] == "H8"
    assert voted["vote_aggregation"] == [{"coordinate": "H8", "count": 1}]
    assert voted["valid_voter_count"] == 1
    assert public_after_vote["my_vote"] is None
    assert "votes" not in public_after_vote
    assert replaced["my_vote"] == "I8"
    assert replaced["vote_aggregation"] == [{"coordinate": "I8", "count": 1}]
    assert removed.status_code == 200
    assert removed.json()["my_vote"] is None
    assert removed.json()["vote_aggregation"] == []


def test_headless_runner_finishes_member_game_and_updates_identity(application: FastAPI) -> None:
    services = application.state.services
    clock = services.headless_clock
    runner = services.turn_resolution
    due_turns = services.headless_due_turns
    assert clock is not None and runner is not None and due_turns is not None
    with (
        TestClient(application, base_url=ORIGIN) as owner,
        TestClient(application, base_url=ORIGIN) as white,
    ):
        owner_csrf = _member(owner, "31")
        white_csrf = _member(white, "32")
        room = _ready_room(owner, owner_csrf, white, white_csrf)
        response = owner.post(
            f"/api/v1/rooms/{room['room_id']}/games",
            headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
            json={"request_id": str(uuid4()), "expected_state_version": room["state_version"]},
        )
        assert response.status_code == 201, response.text
        game_id = response.json()["game_id"]
        pending = owner.get(f"/api/v1/games/{game_id}/result")
        assert pending.status_code == 409
        assert pending.json()["code"] == "GAME_NOT_FINISHED"
        assert owner.get(f"/api/v1/games/{uuid4()}/result").status_code == 404
        assert owner.portal is not None
        for turn_no, expected in (
            (1, TurnProcessingStatus.PASS),
            (2, TurnProcessingStatus.GAME_ENDED),
        ):
            due_turns.values = (DueTurn(str(room["room_id"]), game_id, turn_no),)
            clock.advance(15_000)
            assert owner.portal.call(runner.run_once)[0].status is expected
        current = owner.get(f"/api/v1/rooms/{room['room_id']}/snapshot")
        assert current.status_code == 200
        assert current.json()["status"] == "WAITING"
        assert current.json()["last_game_id"] == game_id
        result = owner.get(f"/api/v1/games/{game_id}/result")
        assert result.status_code == 200, result.text
        assert result.json()["turn_no"] == 2
        assert result.json()["move_no"] == 0
        assert result.json()["board"] == []
        assert result.json()["winning_line"] is None
        assert result.json()["my_rating"] == {
            "outcome": "LOSS",
            "rating_before": 1000,
            "rating_delta": -16,
            "rating_after": 984,
        }
        for private in (
            "member_id",
            "participant_id",
            "rating_adjustments",
            "current_team",
            "turn_status",
            "deadline_ms",
        ):
            assert private not in result.text
        assert all(not p["ready"] for p in current.json()["participants"])
        # Retrying the completed turn must not deduct rating twice.
        assert owner.portal.call(runner.run_once)[0].status is TurnProcessingStatus.GAME_ENDED
        for member_id in (1, 2):
            member = owner.portal.call(services.identity_api.members.find_member, member_id)
            assert member is not None
            assert member.rating == 984


def test_game_access_vote_eligibility_and_stale_version(application: FastAPI) -> None:
    with (
        TestClient(application, base_url=ORIGIN) as owner,
        TestClient(application, base_url=ORIGIN) as white,
        TestClient(application, base_url=ORIGIN) as outsider,
    ):
        owner_csrf = _member(owner, "03")
        white_csrf = _member(white, "04")
        _member(outsider, "05")
        room = _ready_room(owner, owner_csrf, white, white_csrf)
        started = owner.post(
            f"/api/v1/rooms/{room['room_id']}/games",
            headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
            json={
                "request_id": str(uuid4()),
                "expected_state_version": room["state_version"],
            },
        ).json()
        forbidden = white.put(
            f"/api/v1/games/{started['game_id']}/turns/1/vote",
            headers={"Origin": ORIGIN, "X-CSRF-Token": white_csrf},
            json={
                "request_id": str(uuid4()),
                "expected_state_version": started["state_version"],
                "coordinate": "A1",
            },
        )
        stale = owner.put(
            f"/api/v1/games/{started['game_id']}/turns/1/vote",
            headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
            json={
                "request_id": str(uuid4()),
                "expected_state_version": 999,
                "coordinate": "A1",
            },
        )
        hidden = outsider.get(f"/api/v1/games/{started['game_id']}")
        invalid = owner.put(
            f"/api/v1/games/{started['game_id']}/turns/1/vote",
            headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
            json={
                "request_id": str(uuid4()),
                "expected_state_version": started["state_version"],
                "coordinate": "P1",
            },
        )

    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "CURRENT_TEAM_REQUIRED"
    assert stale.status_code == 409
    assert stale.json()["code"] == "STALE_STATE"
    assert stale.json()["current_version"] == started["state_version"]
    assert hidden.status_code == 403
    assert hidden.json()["code"] == "SESSION_NOT_IN_ROOM"
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "INVALID_COORDINATE"


def test_start_conditions_guest_spectator_and_second_game_are_rejected(
    application: FastAPI,
) -> None:
    with (
        TestClient(application, base_url=ORIGIN) as owner,
        TestClient(application, base_url=ORIGIN) as white,
        TestClient(application, base_url=ORIGIN) as guest,
        TestClient(application, base_url=ORIGIN) as anonymous,
    ):
        owner_csrf = _member(owner, "06")
        white_csrf = _member(white, "07")
        guest_csrf = _guest(guest)
        room = _ready_room(owner, owner_csrf, white, white_csrf)
        joined = guest.post(
            f"/api/v1/rooms/{room['room_id']}/joins",
            headers={"Origin": ORIGIN, "X-CSRF-Token": guest_csrf},
            json={
                "request_id": str(uuid4()),
                "expected_state_version": room["state_version"],
            },
        )
        assert joined.status_code == 201
        room = joined.json()
        non_owner = guest.post(
            f"/api/v1/rooms/{room['room_id']}/games",
            headers={"Origin": ORIGIN, "X-CSRF-Token": guest_csrf},
            json={
                "request_id": str(uuid4()),
                "expected_state_version": room["state_version"],
            },
        )
        stale = owner.post(
            f"/api/v1/rooms/{room['room_id']}/games",
            headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
            json={
                "request_id": str(uuid4()),
                "expected_state_version": int(room["state_version"]) - 1,
            },
        )
        started = owner.post(
            f"/api/v1/rooms/{room['room_id']}/games",
            headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
            json={
                "request_id": str(uuid4()),
                "expected_state_version": room["state_version"],
            },
        )
        assert started.status_code == 201
        game = started.json()
        spectator = next(item for item in game["participants"] if item["actor_type"] == "GUEST")
        guest_view = guest.get(f"/api/v1/games/{game['game_id']}")
        spectator_vote = guest.put(
            f"/api/v1/games/{game['game_id']}/turns/1/vote",
            headers={"Origin": ORIGIN, "X-CSRF-Token": guest_csrf},
            json={
                "request_id": str(uuid4()),
                "expected_state_version": game["state_version"],
                "coordinate": "A1",
            },
        )
        second_game = owner.post(
            f"/api/v1/rooms/{room['room_id']}/games",
            headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
            json={
                "request_id": str(uuid4()),
                "expected_state_version": int(room["state_version"]) + 1,
            },
        )
        unauthenticated = anonymous.get(f"/api/v1/games/{game['game_id']}")

    assert non_owner.status_code == 403
    assert non_owner.json()["code"] == "OWNER_REQUIRED"
    assert stale.status_code == 409
    assert stale.json()["code"] == "STALE_STATE"
    assert spectator["role"] == "SPECTATOR"
    assert spectator["team"] is None
    assert guest_view.status_code == 200
    assert guest_view.json()["can_vote"] is False
    assert spectator_vote.status_code == 403
    assert spectator_vote.json()["code"] == "PLAYER_REQUIRED"
    assert second_game.status_code == 409
    assert second_game.json()["code"] == "ROOM_NOT_WAITING"
    assert unauthenticated.status_code == 401


def test_openapi_contains_game_routes_without_provider_internals(application: FastAPI) -> None:
    with TestClient(application, base_url=ORIGIN) as client:
        schema = client.get("/api/openapi.json").json()

    expected = {
        "/api/v1/rooms/{room_id}/games",
        "/api/v1/games/{game_id}",
        "/api/v1/games/{game_id}/result",
        "/api/v1/games/{game_id}/turns/{turn_no}/vote",
    }
    assert expected <= set(schema["paths"])
    response_schema = str(schema["components"]["schemas"]["GameSnapshotResponse"])
    for internal in ("session_digest", "csrf", "redis", "resolver"):
        assert internal not in response_schema.lower()


@pytest.mark.parametrize(
    "code",
    [
        "GAME_RESULT_INCOMPLETE",
        "GAME_RESULT_HISTORY_MISMATCH",
        "GAME_HISTORY_INVALID",
    ],
)
def test_result_corruption_is_reported_as_unavailable(
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    monkeypatch.setattr(
        application.state.services.game_api.games,
        "get_result",
        AsyncMock(side_effect=PersistenceRuleViolation(code)),
    )
    with TestClient(application, base_url=ORIGIN) as client:
        _member(client, "90")
        response = client.get(f"/api/v1/games/{uuid4()}/result")
    assert response.status_code == 503
    assert response.json()["code"] == code
    assert response.headers["content-type"] == "application/problem+json"
    assert "board" not in response.json()
