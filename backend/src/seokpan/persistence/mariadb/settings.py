from pydantic_settings import BaseSettings, SettingsConfigDict


class MigrationSettings(BaseSettings):
    """Settings consumed only by the approved single Alembic execution."""

    model_config = SettingsConfigDict(
        env_prefix="SEOKPAN_",
        extra="ignore",
    )

    migration_database_url: str
