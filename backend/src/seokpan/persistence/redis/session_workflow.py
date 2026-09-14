"""Production Session/Room workflow with provider-backed convergence."""

from __future__ import annotations

import asyncio

from seokpan.identity.application import (
    CreateSession,
    ParticipantSessionPort,
    SessionRecord,
    SessionRuleViolation,
    SessionTransitionUnavailable,
)
from seokpan.persistence.redis.session_adapter import RedisSessionAdapter


class RedisSessionWorkflow:
    """Coordinate Redis Session rotation with Redis Room participation.

    Redis scripts provide compare-and-set/idempotency. No process-local lock or
    uncertainty cache is used as cross-replica authority.
    """

    def __init__(
        self,
        sessions: RedisSessionAdapter,
        participants: ParticipantSessionPort,
    ) -> None:
        self._sessions = sessions
        self._participants = participants

    async def create(self, command: CreateSession) -> SessionRecord:
        return await self._sessions.create(command)

    async def get(self, session_digest: str) -> SessionRecord | None:
        return await self._sessions.get(session_digest)

    async def touch(self, session_digest: str) -> SessionRecord | None:
        return await self._sessions.touch(session_digest)

    async def rotate_identity(
        self,
        *,
        previous: SessionRecord,
        replacement: CreateSession,
    ) -> SessionRecord:
        try:
            rotated = await self._sessions.rotate(
                previous_session_digest=previous.session_digest,
                replacement=replacement,
            )
        except SessionRuleViolation:
            raise
        except BaseException:
            raise SessionTransitionUnavailable from None
        try:
            await self._participants.change_identity(previous, replacement)
        except asyncio.CancelledError:
            # Cancellation cannot prove whether the provider write committed.
            raise
        except SessionTransitionUnavailable:
            # Room write outcome could not be proven. Rolling the Session back
            # could create a confirmed cross-provider identity mismatch.
            raise
        except BaseException:
            try:
                await self._sessions.restore_after_failed_rotation(
                    failed_replacement_digest=replacement.session_digest,
                    previous=previous,
                )
            except BaseException as rollback_error:
                raise SessionTransitionUnavailable from rollback_error
            raise
        return rotated

    async def logout(self, current: SessionRecord) -> bool:
        try:
            await self._participants.leave(current)
            return await self._sessions.revoke(current.session_digest)
        except BaseException:
            raise SessionTransitionUnavailable from None
