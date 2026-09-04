from __future__ import annotations

import pytest

from seokpan.game.application import GameApplicationService
from seokpan.identity.application import SessionActorType, SessionRecord
from seokpan.persistence.memory import (
    InMemoryGamePersistenceAdapter,
    InMemoryRoomRuntimeAdapter,
    InMemoryVoteRuntimeAdapter,
    ManualClock,
)
from seokpan.room.application import RoomApplicationService
from seokpan.room.domain import RoomConfig, Team
from seokpan.vote.application import InitializeVoteRuntime, VoteMutationResult


class UnusedPasswordPort:
    async def encode(self, raw_password: str) -> str:
        raise AssertionError(raw_password)

    async def verify(self, encoded_password: str, candidate_password: str) -> bool:
        raise AssertionError(encoded_password, candidate_password)


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
        csrf_digest=character * 64,
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
            request_id="start",
            expected_state_version=6,
        )

    room_after_failure = await rooms.get(room_id)
    assert room_after_failure is not None
    assert room_after_failure.game_id is not None
    assert len(persistence.games) == 1
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
