from __future__ import annotations

import hashlib

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from seokpan.persistence.memory import ManualClock
from seokpan.persistence.redis.common import RedisKeyspace, RedisProviderError
from seokpan.persistence.redis.room_adapter import RedisRoomRuntimeAdapter
from seokpan.persistence.redis.room_scripts import ROOM_MUTATION

from .conftest import EmulatedRoomRedisClient, create_room


@pytest.mark.asyncio
async def test_mutation_declares_one_hash_slot_and_separated_room_keys() -> None:
    client = EmulatedRoomRedisClient(ManualClock(now_ms=1_000))
    await RedisRoomRuntimeAdapter(client).create(create_room())

    _, numkeys, values = client.evalsha_calls[-1]
    keys = tuple(str(item) for item in values[:numkeys])
    assert numkeys == 10
    assert all("{room-1}" in key for key in keys)
    assert keys[:4] == (
        RedisKeyspace.room_meta("room-1"),
        RedisKeyspace.room_participants("room-1"),
        RedisKeyspace.room_ready("room-1"),
        RedisKeyspace.room_connections("room-1"),
    )
    assert keys[9] == RedisKeyspace.room_game("room-1")
    assert "redis.call('TIME')" in ROOM_MUTATION.source
    assert "expected_version_matches" in ROOM_MUTATION.source
    assert "REQUEST_ID_CONFLICT" in ROOM_MUTATION.source
    assert "HINCRBY', KEYS[9], coordinate, -1" in ROOM_MUTATION.source
    assert "local raw_game = redis.call('GET', KEYS[10])" in ROOM_MUTATION.source
    assert "game.state_version = tonumber(game.state_version or 1) + 1" in (ROOM_MUTATION.source)


@pytest.mark.asyncio
async def test_script_cache_miss_loads_exact_room_source() -> None:
    client = EmulatedRoomRedisClient(ManualClock(now_ms=1_000), scripts_loaded=False)
    await RedisRoomRuntimeAdapter(client).create(create_room())

    assert client.script_load_calls == [ROOM_MUTATION.source]
    assert len(client.evalsha_calls) == 2
    assert (
        ROOM_MUTATION.sha
        == hashlib.sha1(ROOM_MUTATION.source.encode(), usedforsecurity=False).hexdigest()
    )


class FailingRoomClient(EmulatedRoomRedisClient):
    async def evalsha(
        self,
        sha: str,
        numkeys: int,
        *keys_and_args: object,
    ) -> bytes:
        raise RedisConnectionError(
            "redis://user:secret@redis.platform.svc/stone:v1:room:{room-1}:meta"
        )


@pytest.mark.asyncio
async def test_provider_error_is_sanitized() -> None:
    adapter = RedisRoomRuntimeAdapter(FailingRoomClient(ManualClock()))

    with pytest.raises(RedisProviderError) as caught:
        await adapter.create(create_room())

    assert str(caught.value) == "REDIS_PROVIDER_UNAVAILABLE"
    assert "secret" not in str(caught.value)
