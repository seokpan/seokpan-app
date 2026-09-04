from __future__ import annotations

import pytest
from redis.exceptions import ConnectionError

from seokpan.game.domain import Stone
from seokpan.persistence.memory import ManualClock
from seokpan.persistence.redis.common import RedisKeyspace, RedisProviderError
from seokpan.persistence.redis.vote_adapter import RedisVoteRuntimeAdapter
from seokpan.persistence.redis.vote_scripts import VOTE_MUTATION
from seokpan.vote.application import InitializeVoteRuntime
from seokpan.vote.domain import Voter

from .conftest import EmulatedVoteRedisClient


def test_vote_keyspace_uses_one_room_hash_tag() -> None:
    keys = (
        RedisKeyspace.room_game("room-1"),
        RedisKeyspace.room_board("room-1"),
        RedisKeyspace.room_votes("room-1", 1),
        RedisKeyspace.room_vote_tally("room-1", 1),
        RedisKeyspace.room_resolver("room-1", 1),
    )
    assert all("{room-1}" in key for key in keys)
    assert len(set(keys)) == len(keys)
    assert "local function response(value)" in VOTE_MUTATION.source
    assert "return value\nend" in VOTE_MUTATION.source
    assert "return remember(response({" in VOTE_MUTATION.source
    assert "state_version = payload.expected_state_version + 1" in VOTE_MUTATION.source
    assert "game.state_version = current_version(game) + 1" in VOTE_MUTATION.source
    assert "HGET', KEYS[1], 'state_version'" not in VOTE_MUTATION.source
    assert "'status') ~= 'PLAYING'" in VOTE_MUTATION.source
    assert "'game_id') ~= payload.game_id" in VOTE_MUTATION.source


@pytest.mark.asyncio
async def test_script_cache_miss_loads_exact_versioned_script() -> None:
    clock = ManualClock()
    client = EmulatedVoteRedisClient(clock, scripts_loaded=False)
    adapter = RedisVoteRuntimeAdapter(client)

    await adapter.initialize(
        InitializeVoteRuntime(
            "room-1",
            "initialize-1",
            "game-1",
            (Voter("black-1", Stone.BLACK), Voter("white-1", Stone.WHITE)),
            1_000,
            1,
        )
    )

    assert client.script_load_calls == [VOTE_MUTATION.source]
    assert len(client.evalsha_calls) == 2
    assert client.evalsha_calls[0][0] == VOTE_MUTATION.sha
    assert client.evalsha_calls[0][1] == 13


class FailingRedisClient:
    async def get(self, key: str) -> None:
        raise ConnectionError("redis://secret-host:6379")

    async def evalsha(self, sha: str, numkeys: int, *keys_and_args: object) -> object:
        raise ConnectionError("redis://secret-host:6379")

    async def script_load(self, script: str) -> str:
        raise ConnectionError("redis://secret-host:6379")


@pytest.mark.asyncio
async def test_provider_error_is_sanitized() -> None:
    adapter = RedisVoteRuntimeAdapter(FailingRedisClient())
    with pytest.raises(RedisProviderError) as raised:
        await adapter.get("room-1")
    assert str(raised.value) == "REDIS_PROVIDER_UNAVAILABLE"
    assert "secret-host" not in str(raised.value)
