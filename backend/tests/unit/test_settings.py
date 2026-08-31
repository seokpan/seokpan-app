import pytest

from seokpan.settings import Settings


def test_settings_use_seokpan_environment_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEOKPAN_ENVIRONMENT", "test")
    monkeypatch.setenv("SEOKPAN_INSTANCE_ID", "test-instance")

    settings = Settings()

    assert settings.environment == "test"
    assert settings.instance_id == "test-instance"
