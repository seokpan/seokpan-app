from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI

from seokpan.api.game import GameApiServices, game_router
from seokpan.api.identity import IdentityApiServices, identity_router
from seokpan.api.problems import install_problem_handlers
from seokpan.api.realtime import (
    ActiveWebSocketRegistry,
    RealtimeApiServices,
    realtime_router,
)
from seokpan.api.room import RoomApiServices, room_router
from seokpan.game.application import GameApplicationService
from seokpan.health import router as health_router
from seokpan.identity.application import (
    AuthSessionService,
    MemberIdentityService,
)
from seokpan.persistence.memory import (
    InMemoryGamePersistenceAdapter,
    InMemoryIdentityAdapter,
    InMemoryRealtimeEventAdapter,
    InMemoryRoomRuntimeAdapter,
    InMemorySessionAdapter,
    InMemorySessionWorkflow,
    InMemoryVoteRuntimeAdapter,
    ManualClock,
)
from seokpan.room.application import (
    DisconnectExpiryRunner,
    RealtimeEventPort,
    RoomApplicationService,
    RoomConnectionCoordinator,
)
from seokpan.security import (
    Argon2Parameters,
    Argon2PasswordHasher,
    Argon2RoomPassword,
    SecretsTokenSource,
)
from seokpan.settings import Settings


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    identity_api: IdentityApiServices
    room_api: RoomApiServices | None = None
    game_api: GameApiServices | None = None
    realtime_api: RealtimeApiServices | None = None
    disconnect_expiry: DisconnectExpiryRunner | None = None
    headless_clock: ManualClock | None = None


def build_headless_services(
    settings: Settings,
    *,
    realtime_events: RealtimeEventPort | None = None,
) -> ApplicationServices:
    if settings.environment == "production":
        raise RuntimeError("Production provider configuration is required")
    password_hasher = Argon2PasswordHasher(
        Argon2Parameters(time_cost=1, memory_cost_kib=8 * 1024, parallelism=1)
    )
    dummy_hash = password_hasher.hash(SecretsTokenSource().issue())
    members = MemberIdentityService(
        InMemoryIdentityAdapter(),
        password_hasher,
        dummy_password_hash=dummy_hash,
    )
    clock = ManualClock()
    events = realtime_events or InMemoryRealtimeEventAdapter()
    votes = InMemoryVoteRuntimeAdapter(clock)
    room_runtime = InMemoryRoomRuntimeAdapter(clock, vote_connections=votes)
    room_service = RoomApplicationService(
        room_runtime,
        Argon2RoomPassword(password_hasher),
        events,
        votes,
    )
    session_adapter = InMemorySessionAdapter(clock)
    sessions = AuthSessionService(
        InMemorySessionWorkflow(session_adapter, room_service),
        SecretsTokenSource(),
    )
    identity_api = IdentityApiServices(settings, members, sessions, room_service)
    game_service = GameApplicationService(
        rooms=room_service,
        games=InMemoryGamePersistenceAdapter(),
        votes=votes,
        clock=clock,
        events=events,
    )
    room_api = RoomApiServices(identity_api, room_service)
    game_api = GameApiServices(identity_api, game_service)
    connections = RoomConnectionCoordinator(rooms=room_service, votes=votes, clock=clock)
    registry = ActiveWebSocketRegistry()
    return ApplicationServices(
        identity_api,
        room_api,
        game_api,
        RealtimeApiServices(identity_api, room_api, game_api, events, connections, registry),
        DisconnectExpiryRunner(
            due_disconnects=room_runtime,
            connections=connections,
            clock=clock,
        ),
        clock,
    )


def create_app(
    *,
    settings: Settings | None = None,
    services: ApplicationServices | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    resolved_services = services or build_headless_services(resolved_settings)

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        registry = (
            None
            if resolved_services.realtime_api is None
            else resolved_services.realtime_api.registry
        )
        if registry is not None:
            registry.begin_runtime()
        try:
            yield
        finally:
            if registry is not None:
                registry.end_runtime()

    application = FastAPI(
        title="Seokpan API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    install_problem_handlers(application)
    application.include_router(health_router)
    application.include_router(identity_router(resolved_services.identity_api))
    if resolved_services.room_api is not None:
        application.include_router(room_router(resolved_services.room_api))
    if resolved_services.game_api is not None:
        application.include_router(game_router(resolved_services.game_api))
    if resolved_services.realtime_api is not None:
        application.include_router(realtime_router(resolved_services.realtime_api))
    application.state.services = resolved_services
    return application


app = create_app()
