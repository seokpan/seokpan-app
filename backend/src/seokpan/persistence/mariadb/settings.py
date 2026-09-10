from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MigrationSettings(BaseSettings):
    """Settings consumed only by the approved single Alembic execution."""

    model_config = SettingsConfigDict(
        env_prefix="SEOKPAN_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    migration_database_url: str = Field(repr=False)
    database_ca_file: str | None = None
