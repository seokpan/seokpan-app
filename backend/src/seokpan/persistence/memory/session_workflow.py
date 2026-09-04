"""In-memory authentication Session workflow used by Headless tests."""

from __future__ import annotations

from seokpan.identity.application import (
    CreateSession,
    ParticipantSessionPort,
    SessionPort,
    SessionRecord,
    SessionTransitionUnavailable,
)


class InMemorySessionWorkflow:
    """A Fake; real Redis/Room coordination is a later provider gate."""

    def __init__(
        self,
        sessions: SessionPort,
        participants: ParticipantSessionPort | None = None,
    ) -> None:
        self._sessions = sessions
        self._participants = participants or NoopParticipantSessionAdapter()
        self.identity_changes: list[tuple[SessionRecord, CreateSession]] = []
        self.logouts: list[SessionRecord] = []
        self.fail_identity_change = False
        self.fail_logout = False

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
        if self.fail_identity_change:
            raise SessionTransitionUnavailable
        await self._participants.change_identity(previous, replacement)
        result = await self._sessions.rotate(
            previous_session_digest=previous.session_digest,
            replacement=replacement,
        )
        self.identity_changes.append((previous, replacement))
        return result

    async def logout(self, current: SessionRecord) -> bool:
        if self.fail_logout:
            raise SessionTransitionUnavailable
        await self._participants.leave(current)
        revoked = await self._sessions.revoke(current.session_digest)
        if revoked:
            self.logouts.append(current)
        return revoked


class NoopParticipantSessionAdapter:
    """Used only before Room HTTP can create participant state."""

    async def change_identity(
        self,
        previous: SessionRecord,
        replacement: CreateSession,
    ) -> None:
        del previous, replacement

    async def leave(self, current: SessionRecord) -> None:
        del current
