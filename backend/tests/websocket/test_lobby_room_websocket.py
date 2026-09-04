from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from seokpan.app import ApplicationServices, build_headless_services, create_app
from seokpan.identity.application import SessionActorType, SessionRecord, digest_opaque_token
from seokpan.persistence.memory import (
    InMemoryRealtimeEventAdapter,
    InMemoryRoomRuntimeAdapter,
    InMemoryVoteRuntimeAdapter,
    ManualClock,
)
from seokpan.room.application import (
    DisconnectExpiryRunner,
    RealtimeSubscription,
    RoomApplicationService,
    RoomConnectionCoordinator,
)
from seokpan.room.domain import RoomConfig
from seokpan.settings import Settings

ORIGIN = "http://localhost:5173"


class _FailingRealtimeEvents(InMemoryRealtimeEventAdapter):
    async def lobby_rooms_changed(self, payload: Mapping[str, object]) -> None:
        del payload
        raise RuntimeError("EVENT_PROVIDER_UNAVAILABLE")


class _FailingRoomSubscriptionEvents(InMemoryRealtimeEventAdapter):
    async def subscribe_room(self, room_id: str) -> RealtimeSubscription:
        del room_id
        raise RuntimeError("EVENT_PROVIDER_UNAVAILABLE")


class _UnusedRoomPasswords:
    async def encode(self, raw_password: str) -> str:
        return f"$argon2id${raw_password}"

    async def verify(self, encoded_password: str, candidate_password: str) -> bool:
        return encoded_password == f"$argon2id${candidate_password}"


def _session(token: str, actor_type: SessionActorType, actor_id: str) -> SessionRecord:
    return SessionRecord(
        session_digest=digest_opaque_token(token),
        actor_type=actor_type,
        actor_id=actor_id,
        csrf_digest=digest_opaque_token(f"csrf-{token}"),
        created_at_ms=0,
        last_activity_at_ms=0,
        absolute_expires_at_ms=86_400_000,
    )


@pytest.fixture
def headless() -> tuple[FastAPI, ApplicationServices]:
    settings = Settings(environment="test", allowed_origins=(ORIGIN,))
    services = build_headless_services(settings)
    return create_app(settings=settings, services=services), services


def _member(client: TestClient, suffix: str) -> str:
    registered = client.post(
        "/api/v1/members",
        headers={"Origin": ORIGIN},
        json={
            "login_id": f"ws_member_{suffix}",
            "nickname": f"회원{suffix[:6]}",
            "password": "correct-pass",
        },
    )
    assert registered.status_code == 201
    login = client.post(
        "/api/v1/sessions/member",
        headers={"Origin": ORIGIN},
        json={"login_id": f"ws_member_{suffix}", "password": "correct-pass"},
    )
    assert login.status_code == 200
    return str(login.json()["csrf_token"])


def _guest(client: TestClient) -> str:
    response = client.post("/api/v1/sessions/guest", headers={"Origin": ORIGIN})
    assert response.status_code == 201
    return str(response.json()["csrf_token"])


def _upgrade_guest_to_member(client: TestClient, csrf: str, suffix: str) -> str:
    registered = client.post(
        "/api/v1/members",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={
            "login_id": f"ws_member_{suffix}",
            "nickname": f"회원{suffix[:6]}",
            "password": "correct-pass",
        },
    )
    assert registered.status_code == 201
    login = client.post(
        "/api/v1/sessions/member",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={"login_id": f"ws_member_{suffix}", "password": "correct-pass"},
    )
    assert login.status_code == 200
    return str(login.json()["csrf_token"])


def _ws_headers(client: TestClient, *, origin: str = ORIGIN) -> dict[str, str]:
    token = client.cookies.get("seokpan_session")
    assert token is not None
    return {"Origin": origin, "Cookie": f"seokpan_session={token}"}


def _assert_event_envelope(value: dict[str, object]) -> None:
    assert {
        "event_type",
        "schema_version",
        "event_id",
        "occurred_at",
        "state_version",
        "room_id",
        "game_id",
        "turn_no",
        "payload",
    } == set(value)
    assert value["schema_version"] == 1
    UUID(str(value["event_id"]))
    assert str(value["occurred_at"]).endswith("Z")
    assert int(str(value["state_version"])) >= 1
    serialized = repr(value).lower()
    assert "session_digest" not in serialized
    assert "csrf" not in serialized
    assert "encoded_password" not in serialized


def _next_room_event(socket: WebSocketTestSession) -> dict[str, object]:
    while True:
        value = cast(dict[str, object], socket.receive_json())
        if value.get("event_type") != "snapshot.required" or value.get("payload") != {
            "reason": "PARTICIPANT_CONNECTED"
        }:
            return value


def _create_room(client: TestClient, csrf: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/rooms",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={
            "request_id": str(uuid4()),
            "name": "WebSocket 검증방",
            "minimum_ready": 2,
            "vote_seconds": 15,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _join(
    client: TestClient,
    csrf: str,
    room_id: str,
    state_version: int,
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/rooms/{room_id}/joins",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={
            "request_id": str(uuid4()),
            "expected_state_version": state_version,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _room_mutation(
    client: TestClient,
    csrf: str,
    path: str,
    state_version: int,
    **values: object,
) -> dict[str, object]:
    response = client.put(
        path,
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={
            "request_id": str(uuid4()),
            "expected_state_version": state_version,
            **values,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _start_game(
    client: TestClient,
    csrf: str,
    room_id: str,
    state_version: int,
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/rooms/{room_id}/games",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={
            "request_id": str(uuid4()),
            "expected_state_version": state_version,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_websocket_rejects_missing_session_origin_and_query_token(
    headless: tuple[FastAPI, ApplicationServices],
) -> None:
    application, _services = headless
    with TestClient(application, base_url=ORIGIN) as client:
        with (
            pytest.raises(WebSocketDisconnect) as missing_session,
            client.websocket_connect("/ws/v1/lobby", headers={"Origin": ORIGIN}),
        ):
            pass
        assert missing_session.value.code == 4401

        _member(client, "auth")
        with (
            pytest.raises(WebSocketDisconnect) as invalid_origin,
            client.websocket_connect(
                "/ws/v1/lobby",
                headers=_ws_headers(client, origin="https://invalid.example"),
            ),
        ):
            pass
        assert invalid_origin.value.code == 4403

        with (
            pytest.raises(WebSocketDisconnect) as query_token,
            client.websocket_connect(
                "/ws/v1/lobby?token=must-not-be-used",
                headers=_ws_headers(client),
            ),
        ):
            pass
        assert query_token.value.code == 4403


def test_websocket_rejects_expired_session(
    headless: tuple[FastAPI, ApplicationServices],
) -> None:
    application, services = headless
    assert services.headless_clock is not None
    with TestClient(application, base_url=ORIGIN) as client:
        _guest(client)
        services.headless_clock.advance(86_400_001)

        with (
            pytest.raises(WebSocketDisconnect) as expired,
            client.websocket_connect("/ws/v1/lobby", headers=_ws_headers(client)),
        ):
            pass

        assert expired.value.code == 4401


def test_lobby_first_message_and_version_change_only_for_visible_list_change(
    headless: tuple[FastAPI, ApplicationServices],
) -> None:
    application, services = headless
    assert services.realtime_api is not None
    with TestClient(application, base_url=ORIGIN) as client:
        csrf = _member(client, "lobby")
        with client.websocket_connect("/ws/v1/lobby", headers=_ws_headers(client)) as socket:
            snapshot = socket.receive_json()
            _assert_event_envelope(snapshot)
            assert snapshot["event_type"] == "lobby.snapshot"
            assert snapshot["payload"] == {"rooms": []}
            initial_version = int(snapshot["state_version"])

            room = _create_room(client, csrf)
            changed = socket.receive_json()
            _assert_event_envelope(changed)
            assert changed["event_type"] == "lobby.rooms_changed"
            assert changed["state_version"] == initial_version + 1

            after_create = services.realtime_api.events.lobby_version
            _room_mutation(
                client,
                csrf,
                f"/api/v1/rooms/{room['room_id']}/participants/me/team",
                int(room["state_version"]),
                team="BLACK",
            )
            assert services.realtime_api.events.lobby_version == after_create


def test_room_socket_rejects_session_that_did_not_join_room(
    headless: tuple[FastAPI, ApplicationServices],
) -> None:
    application, _services = headless
    with (
        TestClient(application, base_url=ORIGIN) as owner,
        TestClient(application, base_url=ORIGIN) as outsider,
    ):
        owner_csrf = _member(owner, "roomowner")
        room = _create_room(owner, owner_csrf)
        _member(outsider, "outsider")

        with (
            pytest.raises(WebSocketDisconnect) as forbidden,
            outsider.websocket_connect(
                f"/ws/v1/rooms/{room['room_id']}",
                headers=_ws_headers(outsider),
            ),
        ):
            pass

        assert forbidden.value.code == 4403


def test_room_socket_rejects_command_messages(
    headless: tuple[FastAPI, ApplicationServices],
) -> None:
    application, _services = headless
    with TestClient(application, base_url=ORIGIN) as owner:
        owner_csrf = _member(owner, "norpc")
        room = _create_room(owner, owner_csrf)

        with owner.websocket_connect(
            f"/ws/v1/rooms/{room['room_id']}",
            headers=_ws_headers(owner),
        ) as socket:
            snapshot = socket.receive_json()
            _assert_event_envelope(snapshot)
            assert snapshot["event_type"] == "room.snapshot"
            socket.send_json({"command": "ready"})
            with pytest.raises(WebSocketDisconnect) as rejected:
                _next_room_event(socket)

        assert rejected.value.code == 1008


def test_new_room_socket_replaces_old_generation_without_disconnecting_participant(
    headless: tuple[FastAPI, ApplicationServices],
) -> None:
    application, _services = headless
    with (
        TestClient(application, base_url=ORIGIN) as owner,
        TestClient(application, base_url=ORIGIN) as guest,
    ):
        owner_csrf = _member(owner, "replace")
        room = _create_room(owner, owner_csrf)
        guest_csrf = _guest(guest)
        _join(guest, guest_csrf, str(room["room_id"]), int(room["state_version"]))

        with owner.websocket_connect(
            f"/ws/v1/rooms/{room['room_id']}", headers=_ws_headers(owner)
        ) as first:
            assert first.receive_json()["event_type"] == "room.snapshot"
            with owner.websocket_connect(
                f"/ws/v1/rooms/{room['room_id']}", headers=_ws_headers(owner)
            ) as replacement:
                assert replacement.receive_json()["event_type"] == "room.snapshot"
                replaced = _next_room_event(first)
                assert replaced["event_type"] == "connection.reconnect_required"
                current = owner.get(f"/api/v1/rooms/{room['room_id']}/snapshot")
                assert current.status_code == 200
                assert current.json()["participants"][0]["connected"] is True


def test_owner_socket_disconnect_promotes_member_and_clears_ready(
    headless: tuple[FastAPI, ApplicationServices],
) -> None:
    application, _services = headless
    with (
        TestClient(application, base_url=ORIGIN) as owner,
        TestClient(application, base_url=ORIGIN) as member,
    ):
        owner_csrf = _member(owner, "owner")
        room = _create_room(owner, owner_csrf)
        member_csrf = _member(member, "successor")
        room = _join(member, member_csrf, str(room["room_id"]), int(room["state_version"]))
        room = _room_mutation(
            owner,
            owner_csrf,
            f"/api/v1/rooms/{room['room_id']}/participants/me/team",
            int(room["state_version"]),
            team="BLACK",
        )
        room = _room_mutation(
            owner,
            owner_csrf,
            f"/api/v1/rooms/{room['room_id']}/participants/me/ready",
            int(room["state_version"]),
            ready=True,
        )
        room = _room_mutation(
            member,
            member_csrf,
            f"/api/v1/rooms/{room['room_id']}/participants/me/team",
            int(room["state_version"]),
            team="WHITE",
        )
        room = _room_mutation(
            member,
            member_csrf,
            f"/api/v1/rooms/{room['room_id']}/participants/me/ready",
            int(room["state_version"]),
            ready=True,
        )
        successor_id = str(room["participants"][1]["participant_id"])  # type: ignore[index]

        with owner.websocket_connect(
            f"/ws/v1/rooms/{room['room_id']}", headers=_ws_headers(owner)
        ) as socket:
            assert socket.receive_json()["event_type"] == "room.snapshot"

        current = member.get(f"/api/v1/rooms/{room['room_id']}/snapshot")
        assert current.status_code == 200
        assert current.json()["owner_id"] == successor_id
        assert all(not item["ready"] for item in current.json()["participants"])


def test_explicit_leave_closes_that_participants_room_socket(
    headless: tuple[FastAPI, ApplicationServices],
) -> None:
    application, _services = headless
    with (
        TestClient(application, base_url=ORIGIN) as owner,
        TestClient(application, base_url=ORIGIN) as guest,
    ):
        owner_csrf = _member(owner, "leaveowner")
        room = _create_room(owner, owner_csrf)
        guest_csrf = _guest(guest)
        _join(guest, guest_csrf, str(room["room_id"]), int(room["state_version"]))

        with guest.websocket_connect(
            f"/ws/v1/rooms/{room['room_id']}",
            headers=_ws_headers(guest),
        ) as socket:
            snapshot = socket.receive_json()
            left = guest.request(
                "DELETE",
                f"/api/v1/rooms/{room['room_id']}/participants/me",
                headers={"Origin": ORIGIN, "X-CSRF-Token": guest_csrf},
                json={
                    "request_id": str(uuid4()),
                    "expected_state_version": snapshot["state_version"],
                },
            )
            event = _next_room_event(socket)
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()

        assert left.status_code == 200
        assert event["event_type"] == "room.participant_left"
        assert closed.value.code == 1000


def test_socket_disconnect_after_guest_to_member_transition_uses_participant_identity(
    headless: tuple[FastAPI, ApplicationServices],
) -> None:
    application, _services = headless
    with (
        TestClient(application, base_url=ORIGIN) as owner,
        TestClient(application, base_url=ORIGIN) as guest,
    ):
        owner_csrf = _member(owner, "idowner")
        room = _create_room(owner, owner_csrf)
        guest_csrf = _guest(guest)
        joined = _join(
            guest,
            guest_csrf,
            str(room["room_id"]),
            int(room["state_version"]),
        )
        participant_id = joined["participants"][1]["participant_id"]  # type: ignore[index]

        with guest.websocket_connect(
            f"/ws/v1/rooms/{room['room_id']}",
            headers=_ws_headers(guest),
        ) as socket:
            assert socket.receive_json()["event_type"] == "room.snapshot"
            _upgrade_guest_to_member(guest, guest_csrf, "idmember")
            changed = _next_room_event(socket)
            assert changed["event_type"] == "snapshot.required"
            assert changed["payload"] == {"reason": "PARTICIPANT_IDENTITY_CHANGED"}

        current = guest.get(f"/api/v1/rooms/{room['room_id']}/snapshot")
        assert current.status_code == 200
        participant = next(
            item
            for item in current.json()["participants"]
            if item["participant_id"] == participant_id
        )
        assert participant["actor_type"] == "MEMBER"
        assert participant["connected"] is False


def test_disconnect_expiry_runner_removes_participant_once(
    headless: tuple[FastAPI, ApplicationServices],
) -> None:
    application, services = headless
    assert services.disconnect_expiry is not None
    assert services.headless_clock is not None
    with (
        TestClient(application, base_url=ORIGIN) as owner,
        TestClient(application, base_url=ORIGIN) as guest,
    ):
        owner_csrf = _member(owner, "expiry")
        room = _create_room(owner, owner_csrf)
        guest_csrf = _guest(guest)
        joined = _join(guest, guest_csrf, str(room["room_id"]), int(room["state_version"]))

        with guest.websocket_connect(
            f"/ws/v1/rooms/{room['room_id']}", headers=_ws_headers(guest)
        ) as socket:
            assert socket.receive_json()["event_type"] == "room.snapshot"

        before = owner.get(f"/api/v1/rooms/{room['room_id']}/snapshot").json()
        assert len(before["participants"]) == 2
        assert before["participants"][1]["connected"] is False

        assert guest.portal is not None
        assert guest.portal.call(services.disconnect_expiry.run_once) == ()
        services.headless_clock.advance(29_999)
        assert guest.portal.call(services.disconnect_expiry.run_once) == ()
        services.headless_clock.advance(1)
        first = guest.portal.call(services.disconnect_expiry.run_once)
        second = guest.portal.call(services.disconnect_expiry.run_once)

        assert len(first) == 1
        assert second == ()
        after = owner.get(f"/api/v1/rooms/{room['room_id']}/snapshot").json()
        assert len(after["participants"]) == 1
        assert joined["participants"][1]["participant_id"] not in str(after)


def test_planned_shutdown_does_not_record_participant_disconnect(
    headless: tuple[FastAPI, ApplicationServices],
) -> None:
    application, services = headless
    assert services.realtime_api is not None
    with TestClient(application, base_url=ORIGIN) as owner:
        owner_csrf = _member(owner, "shutdown")
        room = _create_room(owner, owner_csrf)

        with owner.websocket_connect(
            f"/ws/v1/rooms/{room['room_id']}",
            headers=_ws_headers(owner),
        ) as socket:
            assert socket.receive_json()["event_type"] == "room.snapshot"
            assert owner.portal is not None
            owner.portal.call(services.realtime_api.registry.end_runtime)
            with pytest.raises(WebSocketDisconnect) as shutdown:
                _next_room_event(socket)

        assert shutdown.value.code == 1012
        current = owner.get(f"/api/v1/rooms/{room['room_id']}/snapshot")
        assert current.status_code == 200
        assert current.json()["participants"][0]["connected"] is True


def test_game_vote_is_removed_when_room_socket_disconnects(
    headless: tuple[FastAPI, ApplicationServices],
) -> None:
    application, _services = headless
    with (
        TestClient(application, base_url=ORIGIN) as owner,
        TestClient(application, base_url=ORIGIN) as member,
    ):
        owner_csrf = _member(owner, "voteowner")
        room = _create_room(owner, owner_csrf)
        member_csrf = _member(member, "votemem")
        room = _join(member, member_csrf, str(room["room_id"]), int(room["state_version"]))
        for client, csrf, team in (
            (owner, owner_csrf, "BLACK"),
            (member, member_csrf, "WHITE"),
        ):
            room = _room_mutation(
                client,
                csrf,
                f"/api/v1/rooms/{room['room_id']}/participants/me/team",
                int(room["state_version"]),
                team=team,
            )
            room = _room_mutation(
                client,
                csrf,
                f"/api/v1/rooms/{room['room_id']}/participants/me/ready",
                int(room["state_version"]),
                ready=True,
            )
        game = _start_game(owner, owner_csrf, str(room["room_id"]), int(room["state_version"]))
        vote = owner.put(
            f"/api/v1/games/{game['game_id']}/turns/{game['turn_no']}/vote",
            headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
            json={
                "request_id": str(uuid4()),
                "expected_state_version": game["state_version"],
                "coordinate": "H8",
            },
        )
        assert vote.status_code == 200, vote.text
        assert vote.json()["vote_aggregation"] == [{"coordinate": "H8", "count": 1}]

        with owner.websocket_connect(
            f"/ws/v1/rooms/{room['room_id']}", headers=_ws_headers(owner)
        ) as socket:
            first = socket.receive_json()
            assert first["event_type"] == "room.snapshot"
            assert first["payload"]["game"] is not None

        current = member.get(f"/api/v1/games/{game['game_id']}")
        assert current.status_code == 200
        assert current.json()["vote_aggregation"] == []
        owner_state = next(
            item
            for item in current.json()["participants"]
            if item["participant_id"] == room["participants"][0]["participant_id"]
        )
        assert owner_state["connected"] is False


def test_waiting_room_close_notifies_guest_to_return_to_lobby(
    headless: tuple[FastAPI, ApplicationServices],
) -> None:
    application, _services = headless
    with (
        TestClient(application, base_url=ORIGIN) as owner,
        TestClient(application, base_url=ORIGIN) as guest,
    ):
        owner_csrf = _member(owner, "close")
        room = _create_room(owner, owner_csrf)
        guest_csrf = _guest(guest)
        room = _join(guest, guest_csrf, str(room["room_id"]), int(room["state_version"]))

        with guest.websocket_connect(
            f"/ws/v1/rooms/{room['room_id']}", headers=_ws_headers(guest)
        ) as socket:
            assert socket.receive_json()["event_type"] == "room.snapshot"
            left = owner.request(
                "DELETE",
                f"/api/v1/rooms/{room['room_id']}/participants/me",
                headers={"Origin": ORIGIN, "X-CSRF-Token": owner_csrf},
                json={
                    "request_id": str(uuid4()),
                    "expected_state_version": room["state_version"],
                },
            )
            closed = _next_room_event(socket)

        assert left.status_code == 200
        assert left.json() is None
        assert closed["event_type"] == "room.closed"
        assert closed["payload"] == {"action": "RETURN_TO_LOBBY"}
        assert guest.get("/api/v1/session").json()["room_id"] is None


def test_event_setup_failure_does_not_change_disconnected_participant() -> None:
    settings = Settings(environment="test", allowed_origins=(ORIGIN,))
    services = build_headless_services(
        settings,
        realtime_events=_FailingRoomSubscriptionEvents(),
    )
    assert services.realtime_api is not None
    application = create_app(settings=settings, services=services)
    with (
        TestClient(application, base_url=ORIGIN) as owner,
        TestClient(application, base_url=ORIGIN) as member,
    ):
        owner_csrf = _member(owner, "setupfail")
        room = _create_room(owner, owner_csrf)
        member_csrf = _member(member, "setupok")
        joined = _join(
            member,
            member_csrf,
            str(room["room_id"]),
            int(room["state_version"]),
        )
        participant = joined["participants"][1]
        assert member.portal is not None
        member.portal.call(
            partial(
                services.realtime_api.connections.disconnect,
                room_id=str(room["room_id"]),
                participant_id=str(participant["participant_id"]),
                connection_generation=1,
            )
        )

        with (
            pytest.raises(WebSocketDisconnect) as failed_setup,
            member.websocket_connect(
                f"/ws/v1/rooms/{room['room_id']}",
                headers=_ws_headers(member),
            ),
        ):
            pass
        assert failed_setup.value.code == 1011

        current = member.get(f"/api/v1/rooms/{room['room_id']}/snapshot")
        assert current.status_code == 200
        assert current.json()["owner_id"] == joined["owner_id"]
        disconnected = next(
            item
            for item in current.json()["participants"]
            if item["participant_id"] == participant["participant_id"]
        )
        assert disconnected["connected"] is False


@pytest.mark.asyncio
async def test_slow_consumer_receives_snapshot_required_instead_of_unbounded_queue() -> None:
    events = InMemoryRealtimeEventAdapter(max_queue_size=1)
    subscription = await events.subscribe_room("room-1")
    await events.room_changed(
        event_type="room.ready_changed",
        room_id="room-1",
        state_version=2,
        payload={},
    )
    await events.room_changed(
        event_type="room.team_changed",
        room_id="room-1",
        state_version=3,
        payload={},
    )

    overflow = await subscription.receive()

    assert overflow.event_type == "snapshot.required"
    assert overflow.payload == {"reason": "SLOW_CONSUMER"}
    assert overflow.state_version == 3
    await subscription.close()


@pytest.mark.asyncio
async def test_participant_left_event_is_delayed_until_disconnect_lease_expires() -> None:
    clock = ManualClock()
    votes = InMemoryVoteRuntimeAdapter(clock)
    runtime = InMemoryRoomRuntimeAdapter(clock, vote_connections=votes)
    events = InMemoryRealtimeEventAdapter()
    rooms = RoomApplicationService(runtime, _UnusedRoomPasswords(), events)
    connections = RoomConnectionCoordinator(rooms=rooms, votes=votes, clock=clock)
    runner = DisconnectExpiryRunner(
        due_disconnects=runtime,
        connections=connections,
        clock=clock,
    )
    owner = _session("owner", SessionActorType.MEMBER, "1")
    guest = _session("guest", SessionActorType.GUEST, "guest-1")
    created = await rooms.create_room(
        session=owner,
        request_id="create-1",
        config=RoomConfig(name="Event Room", minimum_ready=2),
        password=None,
    )
    assert created.snapshot is not None
    joined = await rooms.join_room(
        session=guest,
        room_id=created.snapshot.room_id,
        request_id="join-1",
        expected_state_version=created.snapshot.state_version,
        password=None,
    )
    assert joined.snapshot is not None
    subscription = await events.subscribe_room(created.snapshot.room_id)

    await rooms.disconnect(
        session=guest,
        room_id=created.snapshot.room_id,
        connection_generation=1,
    )
    disconnected = await subscription.receive()

    assert disconnected.event_type == "snapshot.required"
    assert disconnected.payload == {"reason": "PARTICIPANT_DISCONNECTED"}
    assert await runner.run_once() == ()

    clock.advance(30_000)
    assert len(await runner.run_once()) == 1
    left = await subscription.receive()

    assert left.event_type == "room.participant_left"
    assert left.payload == {"participant_id": joined.snapshot.participants[1].participant_id}
    assert await runner.run_once() == ()
    await subscription.close()


def test_event_delivery_failure_does_not_roll_back_completed_http_mutation() -> None:
    settings = Settings(environment="test", allowed_origins=(ORIGIN,))
    services = build_headless_services(
        settings,
        realtime_events=_FailingRealtimeEvents(),
    )
    application = create_app(settings=settings, services=services)

    with TestClient(application, base_url=ORIGIN) as client:
        csrf = _member(client, "eventfail")
        room = _create_room(client, csrf)
        snapshot = client.get(f"/api/v1/rooms/{room['room_id']}/snapshot")

    assert snapshot.status_code == 200
    assert snapshot.json()["room_id"] == room["room_id"]
