"""Lazy production ASGI composition; import and OpenAPI export do no provider I/O."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from seokpan.health import RuntimeReadiness
from seokpan.production import build_production_providers, production_resources
from seokpan.settings import Settings

if TYPE_CHECKING:
    from seokpan.app import ApplicationServices

_LOGGER = logging.getLogger(__name__)


async def _run_background_services(
    services: ApplicationServices, readiness: RuntimeReadiness
) -> None:
    disconnects = services.disconnect_expiry
    turns = services.turn_resolution
    if disconnects is None or turns is None:
        readiness.mark_not_ready()
        raise RuntimeError("PRODUCTION_RUNNERS_REQUIRED")
    try:
        while True:
            await disconnects.run_once()
            await turns.run_once()
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        raise
    except Exception:
        readiness.mark_not_ready()
        realtime = services.realtime_api
        if realtime is not None:
            realtime.registry.end_runtime()
        _LOGGER.error("Production background runner stopped")
        raise


class _RuntimeDispatch:
    def __init__(self, owner: FastAPI) -> None:
        self._owner = owner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        runtime: ASGIApp | None = getattr(self._owner.state, "runtime_application", None)
        if runtime is None:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1013})
            else:
                await JSONResponse(
                    {"status": "not_ready"},
                    status_code=503,
                    headers={"Cache-Control": "no-store"},
                )(scope, receive, send)
            return
        await runtime(scope, receive, send)


def create_production_app(settings: Settings) -> FastAPI:
    """Return an import-safe shell which builds providers only during lifespan startup."""

    if settings.environment != "production":
        raise RuntimeError("Production environment is required")
    readiness = RuntimeReadiness()

    @asynccontextmanager
    async def lifespan(shell: FastAPI) -> AsyncIterator[None]:
        from seokpan.app import build_production_services, create_app

        async with production_resources(settings, readiness) as resources:
            # Resource probes alone are insufficient: routes and mandatory
            # background runners must also be composed before readiness opens.
            readiness.mark_not_ready()
            services = build_production_services(settings, build_production_providers(resources))
            runtime = create_app(settings=settings, services=services, readiness=readiness)
            async with runtime.router.lifespan_context(runtime):
                runner = asyncio.create_task(
                    _run_background_services(services, readiness),
                    name="seokpan-production-runner",
                )
                await asyncio.sleep(0)
                if runner.done():
                    await runner
                shell.state.runtime_application = runtime
                readiness.mark_ready()
                try:
                    yield
                finally:
                    readiness.mark_not_ready()
                    if services.realtime_api is not None:
                        services.realtime_api.registry.end_runtime()
                    runner.cancel()
                    await asyncio.gather(runner, return_exceptions=True)
                    shell.state.runtime_application = None

    shell = FastAPI(lifespan=lifespan, docs_url=None, openapi_url=None, redoc_url=None)
    shell.state.runtime_application = None
    shell.mount("/", _RuntimeDispatch(shell))
    return shell
