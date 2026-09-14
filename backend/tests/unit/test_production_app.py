from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import seokpan.app as app_module
import seokpan.production_app as production_app_module
from seokpan.health import router as health_router
from seokpan.production_app import create_production_app
from seokpan.settings import Settings


def test_production_shell_opens_only_after_services_and_runners_are_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch = monkeypatch
    events: list[str] = []
    disconnect = SimpleNamespace(
        run_once=AsyncMock(side_effect=lambda: events.append("disconnect"))
    )
    turns = SimpleNamespace(run_once=AsyncMock(side_effect=lambda: events.append("turn")))
    registry = SimpleNamespace(end_runtime=Mock(side_effect=lambda: events.append("registry-end")))
    services = SimpleNamespace(
        disconnect_expiry=disconnect,
        turn_resolution=turns,
        realtime_api=SimpleNamespace(registry=registry),
    )
    resource = object()
    providers = object()

    @asynccontextmanager
    async def resources(_settings: Settings, readiness: object) -> AsyncIterator[object]:
        events.append("resources-open")
        readiness.mark_ready()
        try:
            yield resource
        finally:
            events.append("resources-close")

    def create_inner_app(*, settings: Settings, services: object, readiness: object) -> FastAPI:
        del settings, services
        runtime = FastAPI()
        runtime.state.readiness = readiness
        runtime.include_router(health_router)

        @runtime.get("/probe")
        async def probe() -> dict[str, str]:
            return {"status": "ok"}

        return runtime

    patch.setattr(production_app_module, "production_resources", resources)
    patch.setattr(
        production_app_module,
        "build_production_providers",
        lambda value: providers if value is resource else None,
    )
    patch.setattr(
        app_module,
        "build_production_services",
        lambda settings, value: (
            services if settings.environment == "production" and value is providers else None
        ),
    )
    patch.setattr(app_module, "create_app", create_inner_app)

    shell = create_production_app(Settings(environment="production"))
    assert events == []
    with TestClient(shell) as client:
        assert client.get("/probe").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}
        assert disconnect.run_once.await_count >= 1
        assert turns.run_once.await_count >= 1

    assert events[-2:] == ["registry-end", "resources-close"]


def test_production_shell_rejects_missing_mandatory_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch = monkeypatch
    services = SimpleNamespace(
        disconnect_expiry=None,
        turn_resolution=SimpleNamespace(run_once=AsyncMock()),
        realtime_api=None,
    )

    @asynccontextmanager
    async def resources(_settings: Settings, readiness: object) -> AsyncIterator[object]:
        readiness.mark_ready()
        yield object()

    patch.setattr(production_app_module, "production_resources", resources)
    patch.setattr(production_app_module, "build_production_providers", lambda value: value)
    patch.setattr(app_module, "build_production_services", lambda settings, providers: services)
    patch.setattr(app_module, "create_app", lambda **values: FastAPI())

    shell = create_production_app(Settings(environment="production"))
    try:
        with TestClient(shell):
            raise AssertionError("missing production runner must fail startup")
    except RuntimeError as error:
        assert str(error) == "PRODUCTION_RUNNERS_REQUIRED"
