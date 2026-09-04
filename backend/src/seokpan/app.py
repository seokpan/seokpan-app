from dataclasses import dataclass

from fastapi import FastAPI

from seokpan.api.identity import IdentityApiServices, identity_router
from seokpan.api.problems import install_problem_handlers
from seokpan.health import router as health_router
from seokpan.identity.application import (
    AuthSessionService,
    MemberIdentityService,
)
from seokpan.persistence.memory import (
    InMemoryIdentityAdapter,
    InMemorySessionAdapter,
    InMemorySessionWorkflow,
    ManualClock,
)
from seokpan.security import Argon2Parameters, Argon2PasswordHasher, SecretsTokenSource
from seokpan.settings import Settings


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    identity_api: IdentityApiServices


def build_headless_services(settings: Settings) -> ApplicationServices:
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
    session_adapter = InMemorySessionAdapter(ManualClock())
    sessions = AuthSessionService(
        InMemorySessionWorkflow(session_adapter),
        SecretsTokenSource(),
    )
    return ApplicationServices(IdentityApiServices(settings, members, sessions))


def create_app(
    *,
    settings: Settings | None = None,
    services: ApplicationServices | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    resolved_services = services or build_headless_services(resolved_settings)
    application = FastAPI(
        title="Seokpan API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    install_problem_handlers(application)
    application.include_router(health_router)
    application.include_router(identity_router(resolved_services.identity_api))
    application.state.services = resolved_services
    return application


app = create_app()
