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
from seokpan.persistence.redis.common import RedisProviderError
from seokpan.production_app import create_production_app
from seokpan.settings import Settings


def _patch_production_shell(
    monkeypatch: pytest.MonkeyPatch,
    services: object,
    events: list[str],
) -> None:
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

    monkeypatch.setattr(production_app_module, "production_resources", resources)
    monkeypatch.setattr(
        production_app_module,
        "build_production_providers",
        lambda value: providers if value is resource else None,
    )
    monkeypatch.setattr(
        app_module,
        "build_production_services",
        lambda settings, value: (
            services if settings.environment == "production" and value is providers else None
        ),
    )
    monkeypatch.setattr(app_module, "create_app", create_inner_app)


def test_production_shell_opens_only_after_services_and_runners_are_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    _patch_production_shell(monkeypatch, services, events)

    shell = create_production_app(Settings(environment="production"))
    assert events == []
    with TestClient(shell) as client:
        assert client.get("/probe").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}
        assert disconnect.run_once.await_count >= 1
        assert turns.run_once.await_count >= 1

    assert events[-2:] == ["registry-end", "resources-close"]


def test_transient_snapshot_race_does_not_drop_production_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    disconnect = SimpleNamespace(run_once=AsyncMock())
    turns = SimpleNamespace(
        run_once=AsyncMock(side_effect=[RedisProviderError("REDIS_SNAPSHOT_CHANGED"), None])
    )
    registry = SimpleNamespace(end_runtime=Mock(side_effect=lambda: events.append("registry-end")))
    services = SimpleNamespace(
        disconnect_expiry=disconnect,
        turn_resolution=turns,
        realtime_api=SimpleNamespace(registry=registry),
    )
    _patch_production_shell(monkeypatch, services, events)

    shell = create_production_app(Settings(environment="production"))
    with TestClient(shell) as client:
        assert client.get("/health/ready").json() == {"status": "ready"}
        assert client.get("/probe").json() == {"status": "ok"}
        assert turns.run_once.await_count >= 1
        assert registry.end_runtime.call_count == 0


def test_non_transient_provider_failure_still_fails_production_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    services = SimpleNamespace(
        disconnect_expiry=SimpleNamespace(run_once=AsyncMock()),
        turn_resolution=SimpleNamespace(
            run_once=AsyncMock(side_effect=RedisProviderError("REDIS_PROVIDER_UNAVAILABLE"))
        ),
        realtime_api=SimpleNamespace(registry=SimpleNamespace(end_runtime=Mock())),
    )
    _patch_production_shell(monkeypatch, services, events)

    shell = create_production_app(Settings(environment="production"))
    with pytest.raises(RedisProviderError, match="REDIS_PROVIDER_UNAVAILABLE"):
        with TestClient(shell):
            raise AssertionError("non-transient provider failure must fail startup")


def test_production_shell_rejects_missing_mandatory_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = SimpleNamespace(
        disconnect_expiry=None,
        turn_resolution=SimpleNamespace(run_once=AsyncMock()),
        realtime_api=None,
    )

    @asynccontextmanager
    async def resources(_settings: Settings, readiness: object) -> AsyncIterator[object]:
        readiness.mark_ready()
        yield object()

    monkeypatch.setattr(production_app_module, "production_resources", resources)
    monkeypatch.setattr(production_app_module, "build_production_providers", lambda value: value)
    monkeypatch.setattr(app_module, "build_production_services", lambda settings, providers: services)
    monkeypatch.setattr(app_module, "create_app", lambda **values: FastAPI())

    shell = create_production_app(Settings(environment="production"))
    try:
        with TestClient(shell):
            raise AssertionError("missing production runner must fail startup")
    except RuntimeError as error:
        assert str(error) == "PRODUCTION_RUNNERS_REQUIRED"
