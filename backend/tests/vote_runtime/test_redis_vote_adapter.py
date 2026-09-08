from __future__ import annotations

import pytest
from redis.exceptions import ConnectionError

from seokpan.game.domain import Stone
from seokpan.persistence.memory import ManualClock
from seokpan.persistence.redis.common import RedisKeyspace, RedisProviderError, VersionedJsonCodec
from seokpan.persistence.redis.vote_adapter import RedisVoteRuntimeAdapter
from seokpan.persistence.redis.vote_scripts import VOTE_MUTATION, VOTE_READ
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


@pytest.mark.asyncio
async def test_replacement_declares_only_previous_turn_cleanup_keys() -> None:
    client = EmulatedVoteRedisClient(ManualClock())
    adapter = RedisVoteRuntimeAdapter(client)
    voters = (Voter("black-1", Stone.BLACK), Voter("white-1", Stone.WHITE))
    await adapter.initialize(InitializeVoteRuntime("room-1", "start-1", "game-1", voters, 1000, 1))
    client.store._states["room-1"].game.game.finish_system_invalid()
    await adapter.initialize(
        InitializeVoteRuntime(
            "room-1",
            "start-2",
            "game-2",
            voters,
            2000,
            1,
            previous_game_id="game-1",
            previous_turn_no=1,
        )
    )
    _, count, values = client.evalsha_calls[-1]
    assert count == 16
    assert values[13:16] == (
        RedisKeyspace.room_votes("room-1", 1),
        RedisKeyspace.room_vote_tally("room-1", 1),
        RedisKeyspace.room_resolver("room-1", 1),
    )
    assert all("{room-1}" in str(key) for key in values[:count])
    # Static Lua checks, not a claim that a real Redis server executed the script.
    assert "local request_id = 'vote:' .. ARGV[2]" in VOTE_MUTATION.source
    assert "redis.call('DEL', KEYS[14], KEYS[15], KEYS[16])" in VOTE_MUTATION.source
    assert "redis.call('DEL', KEYS[12]" not in VOTE_MUTATION.source
    assert VOTE_MUTATION.source.index("'status') ~= 'PLAYING'") < VOTE_MUTATION.source.index(
        "if cached then"
    )


class ChangedSnapshotClient(EmulatedVoteRedisClient):
    async def evalsha(self, sha: str, numkeys: int, *keys_and_args: object) -> bytes:
        if sha == VOTE_READ.sha:
            payload = VersionedJsonCodec.decode(str(keys_and_args[numkeys]))
            assert payload == {"room_id": "room-1", "game_id": "game-1", "turn_no": 1}
            return self._encode({"ok": False, "error": "REDIS_SNAPSHOT_CHANGED"})
        return await super().evalsha(sha, numkeys, *keys_and_args)


@pytest.mark.asyncio
async def test_read_does_not_mix_new_game_with_previous_turn_keys() -> None:
    adapter = RedisVoteRuntimeAdapter(ChangedSnapshotClient(ManualClock()))
    await adapter.initialize(
        InitializeVoteRuntime(
            "room-1",
            "init",
            "game-1",
            (Voter("black-1", Stone.BLACK), Voter("white-1", Stone.WHITE)),
            1000,
            1,
        )
    )
    with pytest.raises(RedisProviderError, match="REDIS_SNAPSHOT_CHANGED"):
        await adapter.get("room-1")
    assert "game.game_id ~= payload.game_id or game.turn_no ~= payload.turn_no" in VOTE_READ.source


def test_missing_rejection_code_is_provider_failure() -> None:
    with pytest.raises(RedisProviderError, match="REDIS_RESPONSE_INVALID"):
        RedisVoteRuntimeAdapter._raise_rejection({"ok": False, "error": None})
