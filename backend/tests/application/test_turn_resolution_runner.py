from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seokpan.game.application import (
    DueTurn,
    FinalizeGameCommand,
    GameParticipantRecord,
    OfficialMoveRecord,
    PersistenceOutcome,
    PersistenceRuleViolation,
    StartGameCommand,
    TurnFinalizationApproval,
    TurnProcessingStatus,
    TurnResolutionRunner,
)
from seokpan.game.domain import EndReason, GameStatus, Stone
from seokpan.persistence.memory import (
    InMemoryDueTurnSource,
    InMemoryGamePersistenceAdapter,
    InMemoryRoomRuntimeAdapter,
    InMemoryTieSelectionAudit,
    InMemoryTieSelector,
    InMemoryTurnFinalizationGate,
    InMemoryVoteRuntimeAdapter,
    ManualClock,
)
from seokpan.room.application import (
    ChangeRoomTeam,
    CompleteRoomGame,
    CreateRoomRuntime,
    JoinRoomRuntime,
    RoomMutationResult,
    SetRoomReady,
    StartRoomGame,
)
from seokpan.room.domain import ActorType, RoomConfig, RoomStatus, Team
from seokpan.vote.application import (
    AcquireRuntimeResolver,
    ApplyRuntimeResolution,
    CastRuntimeVote,
    CloseRuntimeTurn,
    InitializeVoteRuntime,
    VoteMutationResult,
)
from seokpan.vote.domain import Voter

ROOM_ID = "11111111-1111-4111-8111-111111111111"
GAME_ID = "22222222-2222-4222-8222-222222222222"
BLACK_ID = "33333333-3333-4333-8333-333333333333"
WHITE_ID = "44444444-4444-4444-8444-444444444444"
BLACK_TWO_ID = "55555555-5555-4555-8555-555555555555"


class FailOnceAfterPersistenceVoteAdapter(InMemoryVoteRuntimeAdapter):
    def __init__(self, clock: ManualClock) -> None:
        super().__init__(clock)
        self._clock_for_failure = clock
        self.fail_apply_once = True

    async def apply_resolution(self, command: ApplyRuntimeResolution) -> VoteMutationResult:
        if self.fail_apply_once:
            self.fail_apply_once = False
            self._clock_for_failure.advance(5_001)
        return await super().apply_resolution(command)


class FailOnceRoomCompletionAdapter(InMemoryRoomRuntimeAdapter):
    def __init__(self, clock: ManualClock) -> None:
        super().__init__(clock)
        self.fail_complete_once = True
        self.complete_calls = 0

    async def complete_game(self, command: CompleteRoomGame) -> RoomMutationResult:
        self.complete_calls += 1
        if self.fail_complete_once:
            self.fail_complete_once = False
            raise RuntimeError("simulated Room completion failure")
        return await super().complete_game(command)


class CountingGamePersistenceAdapter(InMemoryGamePersistenceAdapter):
    def __init__(self) -> None:
        super().__init__({1: 1000, 2: 1000, 3: 1000})
        self.append_calls = 0
        self.finalize_calls = 0

    async def append_move(self, command: OfficialMoveRecord) -> PersistenceOutcome:
        self.append_calls += 1
        return await super().append_move(command)

    async def finalize_game(self, command: FinalizeGameCommand) -> PersistenceOutcome:
        self.finalize_calls += 1
        return await super().finalize_game(command)


class UncertainResultPersistenceAdapter(CountingGamePersistenceAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.fail_finalize_once = True

    async def finalize_game(self, command: FinalizeGameCommand) -> PersistenceOutcome:
        outcome = await super().finalize_game(command)
        if self.fail_finalize_once:
            self.fail_finalize_once = False
            raise PersistenceRuleViolation("PERSISTENCE_COMMIT_UNCERTAIN")
        return outcome


class FailBeforeResultPersistenceAdapter(CountingGamePersistenceAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.fail_finalize_once = True

    async def finalize_game(self, command: FinalizeGameCommand) -> PersistenceOutcome:
        if self.fail_finalize_once:
            self.fail_finalize_once = False
            self.finalize_calls += 1
            raise RuntimeError("simulated Result persistence failure")
        return await super().finalize_game(command)


async def setup_runner(
    *,
    gate: TurnFinalizationApproval = TurnFinalizationApproval.ALLOWED,
    tie: str = "A1",
    votes: InMemoryVoteRuntimeAdapter | None = None,
    rooms: InMemoryRoomRuntimeAdapter | None = None,
    games: InMemoryGamePersistenceAdapter | None = None,
) -> tuple[
    TurnResolutionRunner,
    ManualClock,
    InMemoryRoomRuntimeAdapter,
    InMemoryVoteRuntimeAdapter,
    InMemoryGamePersistenceAdapter,
    InMemoryTieSelectionAudit,
]:
    clock = ManualClock()
    room_store = rooms if rooms is not None else InMemoryRoomRuntimeAdapter(clock)
    vote_store = votes if votes is not None else InMemoryVoteRuntimeAdapter(clock)
    member_ratings = {1: 1000, 2: 1000, 3: 1000}
    game_store = games if games is not None else InMemoryGamePersistenceAdapter(member_ratings)
    await room_store.create(
        CreateRoomRuntime(
            ROOM_ID,
            "create",
            RoomConfig(name="runner", minimum_ready=2, vote_seconds=5),
            BLACK_ID,
            "a" * 64,
            None,
        )
    )
    await room_store.join(
        JoinRoomRuntime(ROOM_ID, "join-black", BLACK_TWO_ID, ActorType.MEMBER, "b" * 64, 1)
    )
    await room_store.join(
        JoinRoomRuntime(ROOM_ID, "join-white", WHITE_ID, ActorType.MEMBER, "c" * 64, 2)
    )
    await room_store.change_team(ChangeRoomTeam(ROOM_ID, "black", BLACK_ID, Team.BLACK, 3))
    await room_store.change_team(ChangeRoomTeam(ROOM_ID, "black-two", BLACK_TWO_ID, Team.BLACK, 4))
    await room_store.change_team(ChangeRoomTeam(ROOM_ID, "white", WHITE_ID, Team.WHITE, 5))
    await room_store.set_ready(SetRoomReady(ROOM_ID, "ready-black", BLACK_ID, True, 6))
    await room_store.set_ready(SetRoomReady(ROOM_ID, "ready-black-two", BLACK_TWO_ID, True, 7))
    await room_store.set_ready(SetRoomReady(ROOM_ID, "ready-white", WHITE_ID, True, 8))
    await room_store.start_game(StartRoomGame(ROOM_ID, "start", BLACK_ID, GAME_ID, 9))
    await game_store.start_game(
        StartGameCommand(
            game_id=GAME_ID,
            room_id=ROOM_ID,
            voting_time_seconds=5,
            started_at=datetime.fromtimestamp(0, UTC),
            participants=(
                GameParticipantRecord(BLACK_ID, Stone.BLACK, member_id=1),
                GameParticipantRecord(WHITE_ID, Stone.WHITE, member_id=2),
                GameParticipantRecord(BLACK_TWO_ID, Stone.BLACK, member_id=3),
            ),
        )
    )
    await vote_store.initialize(
        InitializeVoteRuntime(
            ROOM_ID,
            "initialize",
            GAME_ID,
            (
                Voter(BLACK_ID, Stone.BLACK),
                Voter(BLACK_TWO_ID, Stone.BLACK),
                Voter(WHITE_ID, Stone.WHITE),
            ),
            5_000,
            1,
        )
    )
    audit = InMemoryTieSelectionAudit()
    runner = TurnResolutionRunner(
        due_turns=InMemoryDueTurnSource((DueTurn(ROOM_ID, GAME_ID, 1),)),
        finalization_gate=InMemoryTurnFinalizationGate(gate),
        tie_selector=InMemoryTieSelector(tie),
        tie_audit=audit,
        votes=vote_store,
        games=game_store,
        rooms=room_store,
        clock=clock,
        runner_id="runner-a",
    )
    return runner, clock, room_store, vote_store, game_store, audit


@pytest.mark.asyncio
async def test_deadline_and_serviceability_gate_leave_turn_unchanged() -> None:
    runner, clock, _, votes, games, _ = await setup_runner(
        gate=TurnFinalizationApproval.RECOVERY_REQUIRED
    )
    due = DueTurn(ROOM_ID, GAME_ID, 1)
    before = await votes.get(ROOM_ID)

    assert (await runner.process(due)).status is TurnProcessingStatus.NOT_DUE
    clock.advance(5_000)
    assert (await runner.process(due)).status is TurnProcessingStatus.RECOVERY_REQUIRED
    assert await votes.get(ROOM_ID) == before
    assert games.moves == {}
    assert games.results == {}


@pytest.mark.asyncio
async def test_single_candidate_persists_official_move_before_runtime_advances() -> None:
    runner, clock, _, votes, games, _ = await setup_runner()
    snapshot = await votes.get(ROOM_ID)
    assert snapshot is not None
    await votes.cast_vote(
        CastRuntimeVote(ROOM_ID, "vote", GAME_ID, 1, BLACK_ID, "H8", snapshot.state_version)
    )
    clock.advance(5_000)

    result = (await runner.run_once(limit=1))[0]

    assert result.status is TurnProcessingStatus.MOVE
    stored = games.moves[(GAME_ID, 1)]
    assert stored.coordinate.canonical == "H8"
    assert stored.final_vote_count == 1
    assert stored.valid_voter_count == 2
    current = await votes.get(ROOM_ID)
    assert current is not None
    assert current.move_no == 1
    assert current.turn_no == 2

    with pytest.raises(ValueError, match="INVALID_DUE_TURN_LIMIT"):
        await runner.run_once(limit=0)


@pytest.mark.asyncio
async def test_tie_selection_is_domain_checked_and_audited() -> None:
    runner, clock, _, votes, games, audit = await setup_runner(tie="B1")
    snapshot = await votes.get(ROOM_ID)
    assert snapshot is not None
    first = await votes.cast_vote(
        CastRuntimeVote(ROOM_ID, "vote-a", GAME_ID, 1, BLACK_ID, "A1", snapshot.state_version)
    )
    await votes.cast_vote(
        CastRuntimeVote(
            ROOM_ID,
            "vote-b",
            GAME_ID,
            1,
            BLACK_TWO_ID,
            "B1",
            first.snapshot.state_version,
        )
    )
    clock.advance(5_000)

    await runner.process(DueTurn(ROOM_ID, GAME_ID, 1))

    assert games.moves[(GAME_ID, 1)].coordinate.canonical == "B1"
    assert audit.records[0].candidates == ("A1", "B1")
    assert audit.records[0].selected_coordinate == "B1"


@pytest.mark.asyncio
async def test_second_zero_vote_waits_for_result_then_resets_room() -> None:
    runner, clock, rooms, votes, games, _ = await setup_runner()
    clock.advance(5_000)
    first = await runner.process(DueTurn(ROOM_ID, GAME_ID, 1))
    assert first.status is TurnProcessingStatus.PASS
    after_first = await votes.get(ROOM_ID)
    assert after_first is not None
    assert after_first.move_no == 0
    clock.advance(5_000)

    second = await runner.process(DueTurn(ROOM_ID, GAME_ID, 2))

    assert second.status is TurnProcessingStatus.GAME_ENDED
    assert games.results[GAME_ID].result.end_reason is EndReason.JOINT_LOSS
    runtime = await votes.get(ROOM_ID)
    assert runtime is not None
    assert runtime.game_status is GameStatus.FINISHED
    assert runtime.move_no == 0
    room = await rooms.get(ROOM_ID)
    assert room is not None
    assert room.status is RoomStatus.WAITING
    assert room.game_id is None
    assert all(not participant.ready for participant in room.participants)


@pytest.mark.asyncio
async def test_retry_reuses_persisted_move_after_resolver_lease_expiry() -> None:
    clock = ManualClock()
    failing_votes = FailOnceAfterPersistenceVoteAdapter(clock)
    runner, runner_clock, _, votes, games, _ = await setup_runner(votes=failing_votes)
    assert runner_clock is not clock
    snapshot = await votes.get(ROOM_ID)
    assert snapshot is not None
    await votes.cast_vote(
        CastRuntimeVote(ROOM_ID, "vote", GAME_ID, 1, BLACK_ID, "H8", snapshot.state_version)
    )
    runner_clock.advance(5_000)
    clock.advance(5_000)

    first = await runner.process(DueTurn(ROOM_ID, GAME_ID, 1))
    assert first.status is TurnProcessingStatus.RETRY_REQUIRED
    assert len(games.moves) == 1

    runner_clock.advance(1)
    second = await runner.process(DueTurn(ROOM_ID, GAME_ID, 1))
    assert second.status is TurnProcessingStatus.MOVE
    assert len(games.moves) == 1


@pytest.mark.asyncio
async def test_uncertain_result_commit_is_confirmed_before_runtime_and_room_advance() -> None:
    games = UncertainResultPersistenceAdapter()
    runner, clock, rooms, votes, _, _ = await setup_runner(games=games)
    clock.advance(5_000)
    assert (await runner.process(DueTurn(ROOM_ID, GAME_ID, 1))).status is (
        TurnProcessingStatus.PASS
    )
    clock.advance(5_000)
    due = DueTurn(ROOM_ID, GAME_ID, 2)

    with pytest.raises(PersistenceRuleViolation, match="PERSISTENCE_COMMIT_UNCERTAIN"):
        await runner.process(due)

    assert games.finalize_calls == 1
    assert GAME_ID in games.results
    resolving = await votes.get(ROOM_ID)
    assert resolving is not None
    assert resolving.game_status is GameStatus.ACTIVE
    assert (await runner.process(due)).status is TurnProcessingStatus.GAME_ENDED
    assert games.finalize_calls == 1
    room = await rooms.get(ROOM_ID)
    assert room is not None
    assert room.status is RoomStatus.WAITING


@pytest.mark.asyncio
async def test_result_write_failure_retries_before_runtime_and_room_advance() -> None:
    games = FailBeforeResultPersistenceAdapter()
    runner, clock, rooms, votes, _, _ = await setup_runner(games=games)
    clock.advance(5_000)
    assert (await runner.process(DueTurn(ROOM_ID, GAME_ID, 1))).status is (
        TurnProcessingStatus.PASS
    )
    clock.advance(5_000)
    due = DueTurn(ROOM_ID, GAME_ID, 2)

    with pytest.raises(RuntimeError, match="simulated Result persistence failure"):
        await runner.process(due)

    assert GAME_ID not in games.results
    resolving = await votes.get(ROOM_ID)
    assert resolving is not None
    assert resolving.game_status is GameStatus.ACTIVE
    assert (await runner.process(due)).status is TurnProcessingStatus.GAME_ENDED
    assert games.finalize_calls == 2
    room = await rooms.get(ROOM_ID)
    assert room is not None
    assert room.status is RoomStatus.WAITING


@pytest.mark.asyncio
async def test_replayed_due_item_is_stale_after_one_runner_finishes() -> None:
    runner, clock, _, votes, games, _ = await setup_runner()
    snapshot = await votes.get(ROOM_ID)
    assert snapshot is not None
    await votes.cast_vote(
        CastRuntimeVote(ROOM_ID, "vote", GAME_ID, 1, BLACK_ID, "H8", snapshot.state_version)
    )
    clock.advance(5_000)
    due = DueTurn(ROOM_ID, GAME_ID, 1)

    assert (await runner.process(due)).status is TurnProcessingStatus.MOVE
    assert (await runner.process(due)).status is TurnProcessingStatus.STALE
    assert len(games.moves) == 1


@pytest.mark.asyncio
async def test_competing_runner_observes_existing_resolver_lease() -> None:
    runner, clock, _, votes, _, _ = await setup_runner()
    snapshot = await votes.get(ROOM_ID)
    assert snapshot is not None
    voted = await votes.cast_vote(
        CastRuntimeVote(ROOM_ID, "vote", GAME_ID, 1, BLACK_ID, "H8", snapshot.state_version)
    )
    clock.advance(5_000)
    closed = await votes.close_turn(
        CloseRuntimeTurn(ROOM_ID, "manual-close", GAME_ID, 1, voted.snapshot.state_version, 10_000)
    )
    await votes.acquire_resolver(
        AcquireRuntimeResolver(
            ROOM_ID,
            "manual-lease",
            GAME_ID,
            1,
            "other-runner",
            closed.snapshot.state_version,
        )
    )

    result = await runner.process(DueTurn(ROOM_ID, GAME_ID, 1))

    assert result.status is TurnProcessingStatus.RESOLVER_BUSY


@pytest.mark.asyncio
async def test_winning_move_persists_result_before_room_returns_to_waiting() -> None:
    runner, clock, rooms, votes, games, _ = await setup_runner()
    sequence = (
        (BLACK_ID, "A1"),
        (WHITE_ID, "A2"),
        (BLACK_ID, "B1"),
        (WHITE_ID, "B2"),
        (BLACK_ID, "C1"),
        (WHITE_ID, "C2"),
        (BLACK_ID, "D1"),
        (WHITE_ID, "D2"),
        (BLACK_ID, "E1"),
    )

    final = None
    for turn_no, (participant_id, coordinate) in enumerate(sequence, 1):
        snapshot = await votes.get(ROOM_ID)
        assert snapshot is not None
        await votes.cast_vote(
            CastRuntimeVote(
                ROOM_ID,
                f"vote-{turn_no}",
                GAME_ID,
                turn_no,
                participant_id,
                coordinate,
                snapshot.state_version,
            )
        )
        clock.advance(5_000)
        final = await runner.process(DueTurn(ROOM_ID, GAME_ID, turn_no))

    assert final is not None
    assert final.status is TurnProcessingStatus.GAME_ENDED
    assert games.results[GAME_ID].result.end_reason is EndReason.BLACK_WIN
    assert len(games.results[GAME_ID].result.rating_adjustments) == 3
    room = await rooms.get(ROOM_ID)
    assert room is not None
    assert room.status is RoomStatus.WAITING


@pytest.mark.asyncio
async def test_finished_runtime_retry_only_completes_room_after_room_failure() -> None:
    rooms = FailOnceRoomCompletionAdapter(ManualClock())
    games = CountingGamePersistenceAdapter()
    runner, clock, _, votes, _, _ = await setup_runner(rooms=rooms, games=games)
    sequence = (
        (BLACK_ID, "A1"),
        (WHITE_ID, "A2"),
        (BLACK_ID, "B1"),
        (WHITE_ID, "B2"),
        (BLACK_ID, "C1"),
        (WHITE_ID, "C2"),
        (BLACK_ID, "D1"),
        (WHITE_ID, "D2"),
        (BLACK_ID, "E1"),
    )

    for turn_no, (participant_id, coordinate) in enumerate(sequence[:-1], 1):
        snapshot = await votes.get(ROOM_ID)
        assert snapshot is not None
        await votes.cast_vote(
            CastRuntimeVote(
                ROOM_ID,
                f"vote-{turn_no}",
                GAME_ID,
                turn_no,
                participant_id,
                coordinate,
                snapshot.state_version,
            )
        )
        clock.advance(5_000)
        assert (await runner.process(DueTurn(ROOM_ID, GAME_ID, turn_no))).status is (
            TurnProcessingStatus.MOVE
        )

    final_turn = len(sequence)
    participant_id, coordinate = sequence[-1]
    snapshot = await votes.get(ROOM_ID)
    assert snapshot is not None
    await votes.cast_vote(
        CastRuntimeVote(
            ROOM_ID,
            f"vote-{final_turn}",
            GAME_ID,
            final_turn,
            participant_id,
            coordinate,
            snapshot.state_version,
        )
    )
    clock.advance(5_000)
    due = DueTurn(ROOM_ID, GAME_ID, final_turn)

    with pytest.raises(RuntimeError, match="simulated Room completion failure"):
        await runner.process(due)

    finished = await votes.get(ROOM_ID)
    assert finished is not None
    assert finished.game_status is GameStatus.FINISHED
    room_before_retry = await rooms.get(ROOM_ID)
    assert room_before_retry is not None
    assert room_before_retry.status is RoomStatus.PLAYING
    stored_result = games.results[GAME_ID]
    assert (games.append_calls, games.finalize_calls, rooms.complete_calls) == (9, 1, 1)

    retried = await runner.process(due)

    assert retried.status is TurnProcessingStatus.GAME_ENDED
    assert games.results[GAME_ID] == stored_result
    assert (games.append_calls, games.finalize_calls, rooms.complete_calls) == (9, 1, 2)
    room = await rooms.get(ROOM_ID)
    assert room is not None
    assert room.status is RoomStatus.WAITING
    assert room.game_id is None
    assert all(not participant.ready for participant in room.participants)
