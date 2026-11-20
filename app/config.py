"""Application configuration loaded from environment variables / .env."""

from functools import lru_cache
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for PG-NL2SQL."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql://nl2sql_ro:nl2sql_ro_demo_pass@127.0.0.1:55432/nl2sql_demo",
        description="PostgreSQL connection URI (should use a read-only role).",
    )
    gemini_api_key: str = Field(
        default="",
        description="Google Gemini API key. When empty, runs in local heuristic/demo mode.",
    )
    gemini_linker_model: str = Field(
        default="gemini-2.5-flash",
        description="Gemini model used for Stage 1 schema linking.",
    )
    gemini_generator_model: str = Field(
        default="gemini-2.5-pro",
        description="Gemini model used for Stage 2 SQL generation and self-correction.",
    )

    statement_timeout_ms: int = Field(default=10_000, ge=500, le=120_000)
    default_row_limit: int = Field(default=200, ge=1, le=1000)
    max_row_limit: int = Field(default=1000, ge=1, le=10000)
    max_repair_attempts: int = Field(default=3, ge=0, le=5)

    schema_name: str = Field(default="public")
    profile_sample_limit: int = Field(default=500, ge=10, le=5000)
    low_cardinality_limit: int = Field(default=25, ge=2, le=100)
    glossary_path: str = Field(default="glossary.yaml")

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key.strip())

    def resolved_glossary_path(self) -> Path:
        path = Path(self.glossary_path)
        if not path.is_absolute():
            # Resolve relative to project root
            project_root = Path(__file__).resolve().parent.parent
            path = project_root / path
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
