"""Lobby and Room WebSocket snapshot and event delivery."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from seokpan.api.game import GameApiServices, game_snapshot_response
from seokpan.api.identity import SESSION_COOKIE, IdentityApiServices, require_current_session
from seokpan.api.problems import ApiProblem
from seokpan.api.room import (
    LobbyResponse,
    RoomApiServices,
    lobby_room_response,
    room_snapshot_response,
)
from seokpan.identity.application import SessionRecord
from seokpan.room.application import (
    RealtimeEvent,
    RealtimeEventPort,
    RealtimeSubscription,
    RoomConnectionCoordinator,
)
from seokpan.room.domain import RoomRuleViolation


@dataclass(frozen=True, slots=True)
class RealtimeApiServices:
    identity: IdentityApiServices
    rooms: RoomApiServices
    games: GameApiServices | None
    events: RealtimeEventPort
    connections: RoomConnectionCoordinator
    registry: ActiveWebSocketRegistry


class StreamEnd(StrEnum):
    CLIENT_DISCONNECT = "CLIENT_DISCONNECT"
    REPLACED = "REPLACED"
    ROOM_ACCESS_ENDED = "ROOM_ACCESS_ENDED"
    SHUTDOWN = "SHUTDOWN"


class ActiveWebSocketRegistry:
    """Process-local socket ownership; Redis/multi-Replica ownership is a later gate."""

    def __init__(self) -> None:
        self._active: dict[tuple[str, str], tuple[int, asyncio.Event]] = {}
        self._shutdown = asyncio.Event()
        self._runtime_users = 0

    def begin_runtime(self) -> None:
        if self._runtime_users == 0:
            self._shutdown = asyncio.Event()
        self._runtime_users += 1

    def end_runtime(self) -> None:
        if self._runtime_users > 0:
            self._runtime_users -= 1
        if self._runtime_users == 0:
            self._shutdown.set()

    def register(self, room_id: str, participant_id: str, generation: int) -> asyncio.Event:
        key = (room_id, participant_id)
        previous = self._active.get(key)
        if previous is not None:
            previous[1].set()
        replaced = asyncio.Event()
        self._active[key] = (generation, replaced)
        return replaced

    def unregister(self, room_id: str, participant_id: str, generation: int) -> None:
        key = (room_id, participant_id)
        current = self._active.get(key)
        if current is not None and current[0] == generation:
            self._active.pop(key, None)

    async def wait_for_shutdown(self) -> None:
        await self._shutdown.wait()


def realtime_router(services: RealtimeApiServices) -> APIRouter:
    router = APIRouter(tags=["realtime"])

    @router.websocket("/ws/v1/lobby")
    async def lobby_socket(websocket: WebSocket) -> None:
        current = await _websocket_session(websocket, services.identity)
        if current is None:
            return
        subscription: RealtimeSubscription | None = None
        try:
            subscription = await services.events.subscribe_lobby()
            snapshot_version = services.events.lobby_version
            snapshots = await services.rooms.rooms.list_rooms()
            payload = LobbyResponse(
                rooms=[lobby_room_response(item) for item in snapshots]
            ).model_dump(mode="json")
            await websocket.accept()
            await websocket.send_json(
                _snapshot_envelope(
                    "lobby.snapshot",
                    snapshot_version,
                    payload,
                )
            )
            await _stream_events(websocket, subscription, services.registry)
        except WebSocketDisconnect:
            return
        except Exception:
            await _safe_close(websocket, 1011)
        finally:
            if subscription is not None:
                await subscription.close()

    @router.websocket("/ws/v1/rooms/{room_id}")
    async def room_socket(websocket: WebSocket, room_id: str) -> None:
        current = await _websocket_session(websocket, services.identity)
        if current is None:
            return
        participation = services.rooms.rooms.participation(current.session_digest)
        if participation is None or participation.room_id != room_id:
            await websocket.close(code=4403)
            return
        if await services.rooms.rooms.get(room_id) is None:
            await websocket.close(code=4404)
            return

        subscription: RealtimeSubscription | None = None
        generation: int | None = None
        replaced: asyncio.Event | None = None
        established = False
        end = StreamEnd.SHUTDOWN
        try:
            subscription = await services.events.subscribe_room(room_id)
            room = await services.rooms.rooms.get(room_id)
            if room is None:
                raise RoomRuleViolation("ROOM_NOT_FOUND")
            room_payload = await room_snapshot_response(services.rooms, room)
            game_payload = None
            if room.game_id is not None and services.games is not None:
                game_payload = game_snapshot_response(
                    await services.games.games.get_game(session=current, game_id=room.game_id)
                ).model_dump(mode="json")
            await websocket.accept()
            await websocket.send_json(
                _snapshot_envelope(
                    "room.snapshot",
                    room.state_version,
                    {
                        "room": room_payload.model_dump(mode="json"),
                        "game": game_payload,
                    },
                    room_id=room_id,
                    game_id=room.game_id,
                )
            )
            generation, connected_state_version = await services.connections.connect(
                session=current,
                room_id=room_id,
            )
            replaced = services.registry.register(
                room_id,
                participation.participant_id,
                generation,
            )
            established = True
            end = await _stream_events(
                websocket,
                subscription,
                services.registry,
                replaced=replaced,
                room_id=room_id,
                participant_id=participation.participant_id,
                state_version=connected_state_version,
            )
        except WebSocketDisconnect:
            end = StreamEnd.CLIENT_DISCONNECT
        except Exception:
            await _safe_close(websocket, 1011)
        finally:
            if subscription is not None:
                await subscription.close()
            if generation is not None:
                services.registry.unregister(room_id, participation.participant_id, generation)
            if established and generation is not None and end is StreamEnd.CLIENT_DISCONNECT:
                with suppress(ApiProblem, RoomRuleViolation):
                    await services.connections.disconnect(
                        room_id=room_id,
                        participant_id=participation.participant_id,
                        connection_generation=generation,
                    )

    return router


async def _websocket_session(
    websocket: WebSocket,
    identity: IdentityApiServices,
) -> SessionRecord | None:
    if websocket.headers.get("origin") not in identity.settings.allowed_origins:
        await websocket.close(code=4403)
        return None
    prohibited = {"token", "access_token", "session", SESSION_COOKIE}
    if prohibited.intersection(websocket.query_params):
        await websocket.close(code=4403)
        return None
    try:
        return await require_current_session(
            identity,
            websocket.cookies.get(SESSION_COOKIE),
            touch=True,
        )
    except ApiProblem:
        await websocket.close(code=4401)
        return None


async def _stream_events(
    websocket: WebSocket,
    subscription: RealtimeSubscription,
    registry: ActiveWebSocketRegistry,
    *,
    replaced: asyncio.Event | None = None,
    room_id: str | None = None,
    participant_id: str | None = None,
    state_version: int = 1,
) -> StreamEnd:
    receive_task = asyncio.create_task(websocket.receive())
    shutdown_task = asyncio.create_task(registry.wait_for_shutdown())
    replaced_task = None if replaced is None else asyncio.create_task(replaced.wait())
    try:
        while True:
            event_task = asyncio.create_task(subscription.receive())
            receive_wait = cast(asyncio.Future[object], receive_task)
            shutdown_wait = cast(asyncio.Future[object], shutdown_task)
            event_wait = cast(asyncio.Future[object], event_task)
            tasks = {receive_wait, shutdown_wait, event_wait}
            if replaced_task is not None:
                tasks.add(cast(asyncio.Future[object], replaced_task))
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if shutdown_wait in done:
                event_task.cancel()
                await _safe_close(websocket, 1012)
                return StreamEnd.SHUTDOWN
            if replaced_task is not None and cast(asyncio.Future[object], replaced_task) in done:
                event_task.cancel()
                await websocket.send_json(
                    _snapshot_envelope(
                        "connection.reconnect_required",
                        state_version,
                        {"reason": "CONNECTION_REPLACED"},
                        room_id=room_id,
                    )
                )
                await _safe_close(websocket, 4001)
                return StreamEnd.REPLACED
            if receive_wait in done:
                event_task.cancel()
                message = receive_task.result()
                if message["type"] != "websocket.disconnect":
                    await _safe_close(websocket, 1008)
                return StreamEnd.CLIENT_DISCONNECT
            event = event_task.result()
            await websocket.send_json(_event_value(event))
            if event.event_type == "room.closed":
                await _safe_close(websocket, 1000)
                return StreamEnd.ROOM_ACCESS_ENDED
            if (
                event.event_type == "room.participant_left"
                and event.payload.get("participant_id") == participant_id
            ):
                await _safe_close(websocket, 1000)
                return StreamEnd.ROOM_ACCESS_ENDED
            if (
                event.event_type == "snapshot.required"
                and event.payload.get("reason") == "SLOW_CONSUMER"
            ):
                await _safe_close(websocket, 1013)
                return StreamEnd.CLIENT_DISCONNECT
    finally:
        for task in (receive_task, shutdown_task, replaced_task):
            if task is not None and not task.done():
                task.cancel()


def _event_value(event: RealtimeEvent) -> dict[str, object]:
    return {
        "event_type": event.event_type,
        "schema_version": event.schema_version,
        "event_id": event.event_id,
        "occurred_at": event.occurred_at,
        "state_version": event.state_version,
        "room_id": event.room_id,
        "game_id": event.game_id,
        "turn_no": event.turn_no,
        "payload": dict(event.payload),
    }


def _snapshot_envelope(
    event_type: str,
    state_version: int,
    payload: dict[str, object],
    *,
    room_id: str | None = None,
    game_id: str | None = None,
) -> dict[str, object]:
    return _event_value(
        RealtimeEvent(
            event_type=event_type,
            event_id=str(uuid4()),
            occurred_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            state_version=state_version,
            room_id=room_id,
            game_id=game_id,
            payload=payload,
        )
    )


async def _safe_close(websocket: WebSocket, code: int) -> None:
    try:
        await websocket.close(code=code)
    except RuntimeError:
        return
