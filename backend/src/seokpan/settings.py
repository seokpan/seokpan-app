from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SEOKPAN_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    environment: Literal["local", "test", "development", "production"] = "local"
    log_level: str = "INFO"
    instance_id: str = "local"
    identity_database_url: str | None = Field(default=None, repr=False)
    game_database_url: str | None = Field(default=None, repr=False)
    database_ca_file: str | None = None
    redis_url: str | None = None
    allowed_origins: tuple[str, ...] = ("http://localhost:5173",)
