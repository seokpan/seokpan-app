from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from seokpan.chat import (
    ChatDeliveryUnavailable,
    ChatRuleViolation,
    ChatScope,
    ChatScopeType,
    ChatSender,
    SendChat,
)
from seokpan.identity.application import SessionActorType
from seokpan.persistence.redis.chat_adapter import RedisChatAdapter, _message
from seokpan.persistence.redis.chat_scripts import PUBLISH_CHAT
from seokpan.persistence.redis.common import VersionedJsonCodec


class ScriptedClient:
    def __init__(self, result: object) -> None:
        self.result = result
        self.call: tuple[str, int, tuple[object, ...]] | None = None

    async def evalsha(self, sha: str, numkeys: int, *values: object) -> object:
        self.call = (sha, numkeys, values)
        return self.result

    async def script_load(self, script: str) -> bytes | str:
        raise AssertionError(script)


def command() -> SendChat:
    return SendChat(
        scope=ChatScope(ChatScopeType.LOBBY),
        sender=ChatSender(SessionActorType.GUEST, "Guest-0042"),
        sender_key="a" * 64,
        request_id=str(uuid4()),
        text=" hello ",
    )


def result(*, ok: bool, value: object = None, error: object = None) -> str:
    return VersionedJsonCodec.encode({"ok": ok, "value": value, "error": error})


@pytest.mark.asyncio
async def test_publish_uses_scope_keys_and_returns_receipt() -> None:
    response = {
        "message_id": str(uuid4()),
        "occurred_at": "2026-09-14T00:00:00Z",
        "replayed": False,
    }
    client = ScriptedClient(result(ok=True, value=response))
    receipt = await RedisChatAdapter(
        cast(Redis, client), max_recent_requests=8, retry_window_seconds=2
    ).publish(command())

    assert receipt.message_id == response["message_id"]
    assert not receipt.replayed
    assert client.call is not None
    sha, numkeys, values = client.call
    assert sha == PUBLISH_CHAT.sha
    assert numkeys == 3
    assert values[:3] == (
        "stone:v1:chat:{lobby}:receipts",
        "stone:v1:chat:{lobby}:receipt-expiries",
        "stone:v1:chat:{lobby}:messages",
    )
    assert values[5] == 8
    assert values[8] == 2000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "exception"),
    (
        ("REQUEST_ID_REUSED", ChatRuleViolation),
        ("CHAT_RETRY_CAPACITY_REACHED", ChatDeliveryUnavailable),
    ),
)
async def test_publish_maps_known_rejections(error: str, exception: type[Exception]) -> None:
    client = ScriptedClient(result(ok=False, error=error))
    with pytest.raises(exception, match=error):
        await RedisChatAdapter(cast(Redis, client)).publish(command())


@pytest.mark.asyncio
async def test_publish_sanitizes_provider_failure() -> None:
    class FailingClient(ScriptedClient):
        async def evalsha(self, sha: str, numkeys: int, *values: object) -> object:
            raise RedisError("provider details")

    with pytest.raises(ChatDeliveryUnavailable, match="REDIS_PROVIDER_UNAVAILABLE"):
        await RedisChatAdapter(cast(Redis, FailingClient(None))).publish(command())


def test_pubsub_message_is_validated() -> None:
    message_id = str(uuid4())
    value = VersionedJsonCodec.encode(
        {
            "message_id": message_id,
            "occurred_at": "2026-09-14T00:00:00Z",
            "scope": {"kind": "LOBBY", "room_id": None},
            "sender": {"actor_type": "GUEST", "display_name": "Guest-0042"},
            "text": "hello",
        }
    )
    assert _message(value).message_id == message_id
    with pytest.raises(ChatDeliveryUnavailable, match="CHAT_DELIVERY_RESPONSE_INVALID"):
        _message(b"{}")
