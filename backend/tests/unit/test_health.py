from fastapi.testclient import TestClient

from seokpan.app import create_app


def test_health_endpoints_report_initial_process_state() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/health/startup").json() == {"status": "started"}
        assert client.get("/health/live").json() == {"status": "alive"}
        assert client.get("/health/ready").json() == {"status": "ready"}
