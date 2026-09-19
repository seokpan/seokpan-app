from __future__ import annotations

import pytest

from seokpan.game.application import GameApplicationService
from seokpan.identity.application import SessionActorType, SessionRecord, digest_opaque_token
from seokpan.persistence.memory import (
    InMemoryGamePersistenceAdapter,
    InMemoryRealtimeEventAdapter,
    InMemoryRoomRuntimeAdapter,
    InMemoryVoteRuntimeAdapter,
    ManualClock,
)
from seokpan.room.application import RoomApplicationService
from seokpan.room.domain import RoomConfig, RoomRuleViolation, Team
from seokpan.vote.application import InitializeVoteRuntime, VoteMutationResult


class UnusedPasswordPort:
    async def encode(self, raw_password: str) -> str:
        raise AssertionError(raw_password)

    async def verify(self, encoded_password: str, candidate_password: str) -> bool:
        raise AssertionError(encoded_password, candidate_password)


class FailFirstPersistenceAdapter(InMemoryGamePersistenceAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.start_calls = 0

    async def start_game(self, command):
        self.start_calls += 1
        if self.start_calls == 1:
            raise RuntimeError("simulated Game persistence failure")
        return await super().start_game(command)


class FailFirstInitializeAdapter(InMemoryVoteRuntimeAdapter):
    def __init__(self, clock: ManualClock) -> None:
        super().__init__(clock)
        self.initialize_calls = 0

    async def initialize(self, command: InitializeVoteRuntime) -> VoteMutationResult:
        self.initialize_calls += 1
        if self.initialize_calls == 1:
            raise RuntimeError("simulated Vote provider failure")
        return await super().initialize(command)


def _session(character: str, member_id: int) -> SessionRecord:
    return SessionRecord(
        session_digest=character * 64,
        actor_type=SessionActorType.MEMBER,
        actor_id=str(member_id),
        csrf_digest=digest_opaque_token(character * 64),
        csrf_token=character * 64,
        created_at_ms=0,
        last_activity_at_ms=0,
        absolute_expires_at_ms=100_000,
    )


@pytest.mark.asyncio
async def test_start_retry_continues_after_vote_initialization_failure() -> None:
    clock = ManualClock(now_ms=1_000)
    rooms = RoomApplicationService(InMemoryRoomRuntimeAdapter(clock), UnusedPasswordPort())
    owner = _session("a", 1)
    white = _session("b", 2)
    room_result = await rooms.create_room(
        session=owner,
        request_id="create",
        config=RoomConfig(name="retry", minimum_ready=2),
        password=None,
    )
    assert room_result.snapshot is not None
    room_id = room_result.snapshot.room_id
    joined = await rooms.join_room(
        session=white,
        room_id=room_id,
        request_id="join",
        expected_state_version=1,
        password=None,
    )
    assert joined.snapshot is not None
    black_team = await rooms.change_team(
        session=owner,
        request_id="team-black",
        expected_state_version=2,
        team=Team.BLACK,
    )
    assert black_team.snapshot is not None
    black_ready = await rooms.set_ready(
        session=owner,
        request_id="ready-black",
        expected_state_version=3,
        ready=True,
    )
    assert black_ready.snapshot is not None
    white_team = await rooms.change_team(
        session=white,
        request_id="team-white",
        expected_state_version=4,
        team=Team.WHITE,
    )
    assert white_team.snapshot is not None
    white_ready = await rooms.set_ready(
        session=white,
        request_id="ready-white",
        expected_state_version=5,
        ready=True,
    )
    assert white_ready.snapshot is not None

    persistence = InMemoryGamePersistenceAdapter()
    votes = FailFirstInitializeAdapter(clock)
    events = InMemoryRealtimeEventAdapter()
    room_events = await events.subscribe_room(room_id)
    service = GameApplicationService(
        rooms=rooms,
        games=persistence,
        votes=votes,
        clock=clock,
        events=events,
    )

    with pytest.raises(RuntimeError, match="simulated Vote provider failure"):
        await service.start_game(
            session=owner,
            room_id=room_id,
            request_id="start",
            expected_state_version=6,
        )

    room_after_failure = await rooms.get(room_id)
    assert room_after_failure is not None
    assert room_after_failure.game_id is not None
    assert len(persistence.games) == 1
    assert events.room_version(room_id) == 1
    assert events.lobby_version == 1
    clock.advance(5_000)

    completed = await service.start_game(
        session=owner,
        room_id=room_id,
        request_id="start",
        expected_state_version=6,
    )

    assert completed.replayed is True
    assert completed.game.game_id == room_after_failure.game_id
    assert completed.game.deadline_ms == 16_000
    assert len(persistence.games) == 1
    assert votes.initialize_calls == 2
    started = await room_events.receive()
    assert started.event_type == "game.started"
    assert started.game_id == completed.game.game_id
    assert started.turn_no == 1
    assert started.payload == {
        "room_state_version": completed.room.state_version,
        "game_state_version": completed.game.state_version,
        "turn_no": 1,
        "current_team": "BLACK",
        "deadline_ms": 16_000,
    }
    stream_version = events.room_version(room_id)

    replay = await service.start_game(
        session=owner,
        room_id=room_id,
        request_id="start",
        expected_state_version=6,
    )

    assert replay.replayed is True
    assert events.room_version(room_id) == stream_version
    await room_events.close()


@pytest.mark.asyncio
async def test_new_request_recovers_after_game_persistence_failure() -> None:
    clock = ManualClock(now_ms=1_000)
    rooms = RoomApplicationService(InMemoryRoomRuntimeAdapter(clock), UnusedPasswordPort())
    owner = _session("a", 1)
    white = _session("b", 2)
    room_result = await rooms.create_room(
        session=owner,
        request_id="create-persistence",
        config=RoomConfig(name="persistence-retry", minimum_ready=2),
        password=None,
    )
    assert room_result.snapshot is not None
    room_id = room_result.snapshot.room_id
    joined = await rooms.join_room(
        session=white,
        room_id=room_id,
        request_id="join-persistence",
        expected_state_version=1,
        password=None,
    )
    assert joined.snapshot is not None
    black = await rooms.change_team(
        session=owner,
        request_id="team-black-persistence",
        expected_state_version=2,
        team=Team.BLACK,
    )
    assert black.snapshot is not None
    black_ready = await rooms.set_ready(
        session=owner,
        request_id="ready-black-persistence",
        expected_state_version=3,
        ready=True,
    )
    assert black_ready.snapshot is not None
    white_team = await rooms.change_team(
        session=white,
        request_id="team-white-persistence",
        expected_state_version=4,
        team=Team.WHITE,
    )
    assert white_team.snapshot is not None
    white_ready = await rooms.set_ready(
        session=white,
        request_id="ready-white-persistence",
        expected_state_version=5,
        ready=True,
    )
    assert white_ready.snapshot is not None

    persistence = FailFirstPersistenceAdapter()
    votes = InMemoryVoteRuntimeAdapter(clock)
    service = GameApplicationService(
        rooms=rooms,
        games=persistence,
        votes=votes,
        clock=clock,
    )

    with pytest.raises(RuntimeError, match="simulated Game persistence failure"):
        await service.start_game(
            session=owner,
            room_id=room_id,
            request_id="start-failed",
            expected_state_version=6,
        )

    partial = await rooms.get(room_id)
    assert partial is not None
    assert partial.status.value == "PLAYING"
    assert partial.game_id is not None
    assert await votes.get(room_id) is None
    assert persistence.games == {}

    clock.advance(2_000)
    recovered = await service.start_game(
        session=owner,
        room_id=room_id,
        request_id="start-recover",
        expected_state_version=partial.state_version,
    )

    assert recovered.replayed is True
    assert recovered.game.game_id == partial.game_id
    assert recovered.game.deadline_ms == 18_000
    assert len(persistence.games) == 1
    assert persistence.start_calls == 2


@pytest.mark.asyncio
async def test_new_request_recovers_after_vote_initialization_failure() -> None:
    clock = ManualClock(now_ms=1_000)
    rooms = RoomApplicationService(InMemoryRoomRuntimeAdapter(clock), UnusedPasswordPort())
    owner = _session("a", 1)
    white = _session("b", 2)
    room_result = await rooms.create_room(
        session=owner,
        request_id="create-vote",
        config=RoomConfig(name="vote-retry", minimum_ready=2),
        password=None,
    )
    assert room_result.snapshot is not None
    room_id = room_result.snapshot.room_id
    joined = await rooms.join_room(
        session=white,
        room_id=room_id,
        request_id="join-vote",
        expected_state_version=1,
        password=None,
    )
    assert joined.snapshot is not None
    black = await rooms.change_team(
        session=owner,
        request_id="team-black-vote",
        expected_state_version=2,
        team=Team.BLACK,
    )
    assert black.snapshot is not None
    black_ready = await rooms.set_ready(
        session=owner,
        request_id="ready-black-vote",
        expected_state_version=3,
        ready=True,
    )
    assert black_ready.snapshot is not None
    white_team = await rooms.change_team(
        session=white,
        request_id="team-white-vote",
        expected_state_version=4,
        team=Team.WHITE,
    )
    assert white_team.snapshot is not None
    white_ready = await rooms.set_ready(
        session=white,
        request_id="ready-white-vote",
        expected_state_version=5,
        ready=True,
    )
    assert white_ready.snapshot is not None

    persistence = InMemoryGamePersistenceAdapter()
    votes = FailFirstInitializeAdapter(clock)
    service = GameApplicationService(
        rooms=rooms,
        games=persistence,
        votes=votes,
        clock=clock,
    )

    with pytest.raises(RuntimeError, match="simulated Vote provider failure"):
        await service.start_game(
            session=owner,
            room_id=room_id,
            request_id="start-vote-failed",
            expected_state_version=6,
        )

    partial = await rooms.get(room_id)
    assert partial is not None
    assert partial.game_id is not None
    assert len(persistence.games) == 1
    assert await votes.get(room_id) is None

    clock.advance(3_000)
    recovered = await service.start_game(
        session=owner,
        room_id=room_id,
        request_id="start-vote-recover",
        expected_state_version=partial.state_version,
    )

    assert recovered.replayed is True
    assert recovered.game.game_id == partial.game_id
    assert recovered.game.deadline_ms == 19_000
    assert len(persistence.games) == 1
    assert votes.initialize_calls == 2


@pytest.mark.asyncio
async def test_new_start_request_does_not_mask_an_already_complete_game_start() -> None:
    clock = ManualClock(now_ms=1_000)
    rooms = RoomApplicationService(InMemoryRoomRuntimeAdapter(clock), UnusedPasswordPort())
    owner = _session("a", 1)
    white = _session("b", 2)
    room_result = await rooms.create_room(
        session=owner,
        request_id="create-complete",
        config=RoomConfig(name="complete-start", minimum_ready=2),
        password=None,
    )
    assert room_result.snapshot is not None
    room_id = room_result.snapshot.room_id
    joined = await rooms.join_room(
        session=white,
        room_id=room_id,
        request_id="join-complete",
        expected_state_version=1,
        password=None,
    )
    assert joined.snapshot is not None
    black = await rooms.change_team(
        session=owner,
        request_id="team-black-complete",
        expected_state_version=2,
        team=Team.BLACK,
    )
    assert black.snapshot is not None
    black_ready = await rooms.set_ready(
        session=owner,
        request_id="ready-black-complete",
        expected_state_version=3,
        ready=True,
    )
    assert black_ready.snapshot is not None
    white_team = await rooms.change_team(
        session=white,
        request_id="team-white-complete",
        expected_state_version=4,
        team=Team.WHITE,
    )
    assert white_team.snapshot is not None
    white_ready = await rooms.set_ready(
        session=white,
        request_id="ready-white-complete",
        expected_state_version=5,
        ready=True,
    )
    assert white_ready.snapshot is not None

    persistence = InMemoryGamePersistenceAdapter()
    votes = InMemoryVoteRuntimeAdapter(clock)
    service = GameApplicationService(
        rooms=rooms,
        games=persistence,
        votes=votes,
        clock=clock,
    )
    started = await service.start_game(
        session=owner,
        room_id=room_id,
        request_id="start-complete",
        expected_state_version=6,
    )

    with pytest.raises(RoomRuleViolation, match="ROOM_NOT_WAITING"):
        await service.start_game(
            session=owner,
            room_id=room_id,
            request_id="another-start",
            expected_state_version=started.room.state_version,
        )
