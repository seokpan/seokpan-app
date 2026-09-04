"""Deterministic in-memory Vote runtime adapter."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from seokpan.game.domain import Game
from seokpan.persistence.memory.session_adapter import ManualClock
from seokpan.room.application import ROOM_REQUEST_DEDUPE_TTL_MS
from seokpan.vote.application import (
    RESOLVER_LEASE_MS,
    AcquireRuntimeResolver,
    ApplyRuntimeResolution,
    CastRuntimeVote,
    CloseRuntimeTurn,
    InitializeVoteRuntime,
    RemoveRuntimeVote,
    ResolverLease,
    VoteMutationResult,
    VoteRuntimeSnapshot,
)
from seokpan.vote.domain import TurnClosure, TurnStatus, VoteRuleViolation, VoteTally, VoteTurnGame


class _VoteCommand(Protocol):
    @property
    def room_id(self) -> str: ...

    @property
    def request_id(self) -> str: ...

    @property
    def game_id(self) -> str: ...

    @property
    def expected_state_version(self) -> int: ...


@dataclass(slots=True)
class _VoteState:
    game: VoteTurnGame
    state_version: int
    resolver: ResolverLease | None = None
    closure: TurnClosure | None = None
    valid_voter_count: int | None = None


@dataclass(frozen=True, slots=True)
class _CachedResult:
    fingerprint: str
    result: VoteMutationResult
    expires_at_ms: int


class InMemoryVoteRuntimeAdapter:
    """A Fake for contract tests; passing it is not Redis Provider evidence."""

    def __init__(self, clock: ManualClock) -> None:
        self._clock = clock
        self._states: dict[str, _VoteState] = {}
        self._requests: dict[tuple[str, str], _CachedResult] = {}

    async def initialize(self, command: InitializeVoteRuntime) -> VoteMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        if command.room_id in self._states:
            raise VoteRuleViolation("GAME_RUNTIME_ALREADY_EXISTS")
        state = _VoteState(
            game=VoteTurnGame(
                game_id=command.game_id,
                participants=command.participants,
                deadline_ms=command.deadline_ms,
                game=Game(),
            ),
            state_version=command.expected_state_version + 1,
        )
        self._states[command.room_id] = state
        return self._remember(command, VoteMutationResult(self._snapshot(command.room_id, state)))

    async def get(self, room_id: str) -> VoteRuntimeSnapshot | None:
        state = self._states.get(room_id)
        return None if state is None else self._snapshot(room_id, state)

    async def cast_vote(self, command: CastRuntimeVote) -> VoteMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require(command)
        before = state.game.votes
        state.game.cast_vote(
            game_id=command.game_id,
            turn_no=command.turn_no,
            participant_id=command.participant_id,
            coordinate=command.coordinate,
            now_ms=self._clock.now_ms,
        )
        if state.game.votes != before:
            state.state_version += 1
        return self._remember(command, VoteMutationResult(self._snapshot(command.room_id, state)))

    async def remove_vote(self, command: RemoveRuntimeVote) -> VoteMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require(command)
        before = state.game.votes
        state.game.remove_vote(
            game_id=command.game_id,
            turn_no=command.turn_no,
            participant_id=command.participant_id,
            now_ms=self._clock.now_ms,
        )
        if state.game.votes != before:
            state.state_version += 1
        return self._remember(command, VoteMutationResult(self._snapshot(command.room_id, state)))

    async def close_turn(self, command: CloseRuntimeTurn) -> VoteMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require(command)
        valid_voter_count = sum(
            item.connected and item.role.value == "PLAYER" and item.team is state.game.current_team
            for item in state.game.participants
        )
        closure = state.game.close_voting(
            game_id=command.game_id,
            turn_no=command.turn_no,
            now_ms=self._clock.now_ms,
            next_deadline_ms=command.next_deadline_ms,
        )
        state.closure = closure if closure.status is TurnStatus.RESOLVING else None
        state.valid_voter_count = valid_voter_count
        state.state_version += 1
        return self._remember(
            command,
            VoteMutationResult(
                self._snapshot(command.room_id, state, closure=closure),
                closure=closure,
                valid_voter_count=valid_voter_count,
            ),
        )

    async def acquire_resolver(self, command: AcquireRuntimeResolver) -> VoteMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require(command)
        if state.game.turn_status is not TurnStatus.RESOLVING:
            raise VoteRuleViolation("TURN_NOT_RESOLVING")
        current = state.resolver
        if (
            current is not None
            and current.expires_at_ms > self._clock.now_ms
            and current.resolution_id != command.resolution_id
        ):
            raise VoteRuleViolation("RESOLVER_LEASE_HELD")
        state.resolver = ResolverLease(
            resolution_id=command.resolution_id,
            expires_at_ms=self._clock.now_ms + RESOLVER_LEASE_MS,
        )
        return self._remember(command, VoteMutationResult(self._snapshot(command.room_id, state)))

    async def apply_resolution(self, command: ApplyRuntimeResolution) -> VoteMutationResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        state = self._require(command)
        resolver = state.resolver
        if resolver is None or resolver.resolution_id != command.resolution_id:
            raise VoteRuleViolation("RESOLVER_NOT_OWNER")
        if resolver.expires_at_ms <= self._clock.now_ms:
            raise VoteRuleViolation("RESOLVER_LEASE_EXPIRED")
        if command.resolution.result.value == "JOINT_LOSS":
            resolution = state.game.resolve_joint_loss(
                game_id=command.game_id,
                turn_no=command.turn_no,
            )
        else:
            resolution = state.game.resolve_move(
                game_id=command.game_id,
                turn_no=command.turn_no,
                selected_coordinate=command.resolution.selected_coordinate,
                next_deadline_ms=command.next_deadline_ms,
            )
        if resolution != command.resolution:
            raise VoteRuleViolation("RESOLUTION_MISMATCH")
        state.resolver = None
        state.closure = None
        state.state_version += 1
        return self._remember(
            command,
            VoteMutationResult(
                self._snapshot(command.room_id, state),
                resolution=resolution,
            ),
        )

    def _require(self, command: _VoteCommand) -> _VoteState:
        state = self._states.get(command.room_id)
        if state is None:
            raise VoteRuleViolation("GAME_RUNTIME_NOT_FOUND")
        if state.game.game_id != command.game_id:
            raise VoteRuleViolation("STALE_GAME")
        if state.state_version != command.expected_state_version:
            raise VoteRuleViolation("STATE_VERSION_CONFLICT")
        return state

    def _snapshot(
        self,
        room_id: str,
        state: _VoteState,
        *,
        closure: TurnClosure | None = None,
    ) -> VoteRuntimeSnapshot:
        votes = state.game.votes
        counts = Counter(item.coordinate for item in votes)
        active_closure = closure or state.closure
        tally = (
            active_closure.tally
            if active_closure is not None
            else tuple(
                VoteTally(coordinate=coordinate, count=count)
                for coordinate, count in sorted(
                    counts.items(), key=lambda item: (-item[1], item[0].canonical)
                )
            )
        )
        candidates = () if active_closure is None else active_closure.candidates
        return VoteRuntimeSnapshot(
            room_id=room_id,
            game_id=state.game.game_id,
            state_version=state.state_version,
            turn_no=state.game.turn_no,
            turn_status=state.game.turn_status,
            current_team=state.game.current_team,
            deadline_ms=state.game.deadline_ms,
            consecutive_passes=state.game.consecutive_passes,
            move_no=state.game.game.move_no,
            game_status=state.game.game.status,
            end_reason=state.game.game.end_reason,
            participants=state.game.participants,
            votes=votes,
            tally=tally,
            candidates=candidates,
            occupied_cells=state.game.game.occupied_cells,
            resolver=state.resolver,
            valid_voter_count=state.valid_voter_count,
        )

    def _replay(self, command: _VoteCommand) -> VoteMutationResult | None:
        self._purge_expired_requests()
        cached = self._requests.get((command.room_id, command.request_id))
        if cached is None:
            return None
        if cached.fingerprint != self._fingerprint(command):
            raise VoteRuleViolation("REQUEST_ID_CONFLICT")
        result = cached.result
        return VoteMutationResult(
            snapshot=result.snapshot,
            replayed=True,
            closure=result.closure,
            resolution=result.resolution,
            valid_voter_count=result.valid_voter_count,
        )

    def _remember(self, command: _VoteCommand, result: VoteMutationResult) -> VoteMutationResult:
        self._requests[(command.room_id, command.request_id)] = _CachedResult(
            fingerprint=self._fingerprint(command),
            result=result,
            expires_at_ms=self._clock.now_ms + ROOM_REQUEST_DEDUPE_TTL_MS,
        )
        return result

    def _purge_expired_requests(self) -> None:
        expired = [
            key
            for key, value in self._requests.items()
            if value.expires_at_ms <= self._clock.now_ms
        ]
        for key in expired:
            self._requests.pop(key, None)

    @staticmethod
    def _fingerprint(command: _VoteCommand) -> str:
        return hashlib.sha256(repr(command).encode()).hexdigest()
