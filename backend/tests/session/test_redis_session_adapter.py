from __future__ import annotations

import hashlib

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from seokpan.persistence.memory import ManualClock
from seokpan.persistence.redis.common import (
    RedisKeyspace,
    RedisProviderError,
    VersionedJsonCodec,
)
from seokpan.persistence.redis.session_adapter import RedisSessionAdapter
from seokpan.persistence.redis.session_scripts import CREATE_SESSION

from .conftest import (
    EmulatedRedisClient,
    digest,
    expected_session_key,
    guest_command,
    member_command,
)


@pytest.mark.asyncio
async def test_redis_adapter_uses_official_key_family_and_declared_member_index(
    emulated_redis_client: EmulatedRedisClient,
) -> None:
    adapter = RedisSessionAdapter(emulated_redis_client)
    await adapter.create(member_command())

    _, numkeys, values = emulated_redis_client.evalsha_calls[-1]
    assert numkeys == 2
    assert values[:2] == (
        expected_session_key("b"),
        RedisKeyspace.member_sessions("42"),
    )
    assert "stone:v1:" in CREATE_SESSION.source


@pytest.mark.asyncio
async def test_script_cache_miss_loads_exact_versioned_source_once() -> None:
    client = EmulatedRedisClient(clock=ManualClock(now_ms=1_000), scripts_loaded=False)
    adapter = RedisSessionAdapter(client)

    await adapter.create(guest_command())

    assert client.script_load_calls == [CREATE_SESSION.source]
    assert len(client.evalsha_calls) == 2
    assert client.evalsha_calls[0][0] == CREATE_SESSION.sha
    assert (
        CREATE_SESSION.sha
        == hashlib.sha1(CREATE_SESSION.source.encode(), usedforsecurity=False).hexdigest()
    )


@pytest.mark.asyncio
async def test_get_decodes_versioned_utf8_json(
    emulated_redis_client: EmulatedRedisClient,
) -> None:
    adapter = RedisSessionAdapter(emulated_redis_client)
    created = await adapter.create(guest_command())

    assert await adapter.get(digest("a")) == created
    assert emulated_redis_client.get_keys == [expected_session_key("a")]


class InvalidResponseClient(EmulatedRedisClient):
    async def get(self, key: str) -> bytes:
        return b"not-json"


@pytest.mark.asyncio
async def test_invalid_provider_payload_is_a_sanitized_error() -> None:
    adapter = RedisSessionAdapter(InvalidResponseClient(ManualClock()))

    with pytest.raises(RedisProviderError, match="REDIS_RESPONSE_INVALID"):
        await adapter.get(digest("a"))


class FailingClient(EmulatedRedisClient):
    async def get(self, key: str) -> bytes | None:
        raise RedisConnectionError("redis://user:secret@internal/stone:v1:session:secret")


@pytest.mark.asyncio
async def test_provider_error_does_not_expose_url_key_or_token() -> None:
    adapter = RedisSessionAdapter(FailingClient(ManualClock()))

    with pytest.raises(RedisProviderError) as caught:
        await adapter.get(digest("a"))

    assert str(caught.value) == "REDIS_PROVIDER_UNAVAILABLE"
    assert "secret" not in str(caught.value)


def test_json_codec_is_deterministic_versioned_json() -> None:
    encoded = VersionedJsonCodec.encode({"value": "한글", "schema_version": 1})

    assert encoded == '{"schema_version":1,"value":"\\ud55c\\uae00"}'
    assert VersionedJsonCodec.decode(encoded) == {"schema_version": 1, "value": "한글"}
