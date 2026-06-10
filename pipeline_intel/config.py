from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://pipeline:pipeline@localhost:5433/pipeline_intel"

    artifact_backend: str = "local"  # local | s3
    artifact_local_dir: str = "./artifacts"
    artifact_s3_endpoint: str | None = None
    artifact_s3_bucket: str = "raw-artifacts"
    artifact_s3_access_key: str | None = None
    artifact_s3_secret_key: str | None = None

    anthropic_api_key: str | None = None

    crawler_user_agent: str = "PipelineIntelBot/0.1"
    crawler_delay_seconds: float = 1.0


@lru_cache
def settings() -> Settings:
    return Settings()
