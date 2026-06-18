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
    extraction_timeout_seconds: int = 240
    qa_timeout_seconds: int = 120

    crawler_user_agent: str = "PipelineIntelBot/0.1"
    crawler_delay_seconds: float = 1.0
    # Some sites (e.g. Lilly, Vertex) bot-block a non-browser UA at the edge. The headless
    # browser renders with a realistic UA; robots compliance + rate limiting still apply.
    browser_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )


@lru_cache
def settings() -> Settings:
    return Settings()
