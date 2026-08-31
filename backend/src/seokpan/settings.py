from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SEOKPAN_",
        extra="ignore",
    )

    environment: Literal["local", "test", "development", "production"] = "local"
    log_level: str = "INFO"
    instance_id: str = "local"
