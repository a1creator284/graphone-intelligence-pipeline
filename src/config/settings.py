"""
Centralized application configuration.

All configuration comes from environment variables (optionally loaded from a
.env file in local development). Nothing here is hardcoded to a secret value.
See .env.example for the full list of supported variables.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Requirement 4.10: CLOCK_SKEW_TOLERANCE_SECONDS must not exceed 300s (5min).
# This tolerance exists for minor server clock drift on "just published"
# articles, not for accepting genuinely future-scheduled content.
MAX_CLOCK_SKEW_TOLERANCE_SECONDS = 300


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Environment -------------------------------------------------
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # ---- Datastores ----------------------------------------------------
    # Defaults point at local dev services (see docker-compose.yml). In
    # production these MUST be overridden via environment variables.
    database_url: str = Field(
        default="postgresql+asyncpg://graphone:graphone@localhost:5432/graphone"
    )
    redis_url: str = Field(default="redis://localhost:6379/0")

    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_seconds: int = 30

    # ---- LLM providers ---------------------------------------------------
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    groq_api_key: str | None = None
    groq_model: str | None = None
    deepseek_api_key: str | None = None
    deepseek_model: str | None = None

    # Fallback order the orchestrator walks through. Configurable so a
    # provider outage doesn't require a code change.
    llm_provider_order: list[str] = Field(default_factory=lambda: ["gemini", "groq", "deepseek"])

    llm_request_timeout_seconds: float = 60.0
    llm_max_retries: int = 3
    # Approximate token budget per provider/model; used by the chunker to
    # decide when a document needs to be split (see extraction/chunker.py).
    llm_token_budget: int = 12000

    # ---- OpenAlex --------------------------------------------------------
    # OpenAlex asks unauthenticated clients to identify themselves with a
    # `mailto=` parameter to enter the faster "polite pool". It is optional:
    # when unset the adapter simply omits the parameter rather than sending
    # a made-up address.
    openalex_mailto: str | None = None

    # ---- Y Combinator company directory ----------------------------------
    # The YC directory's own search UI ships a public, search-only Algolia
    # app id + key in the page source (window.AlgoliaOpts). They are not
    # secrets, but YC may rotate them, so they are overridable here without
    # a code change. Unset -> the adapter uses the values captured from the
    # live page.
    yc_algolia_app_id: str | None = None
    yc_algolia_api_key: str | None = None

    # ---- GitHub enrichment ----------------------------------------------
    github_token: str | None = None
    github_api_base_url: str = "https://api.github.com"

    # ---- Google Sheets export --------------------------------------------
    google_service_account_json: str | None = None  # raw JSON or a file path
    google_sheet_id: str | None = None

    # ---- Crawling / concurrency ------------------------------------------
    max_concurrency: int = 20
    request_timeout_seconds: float = 20.0
    max_retries: int = 5
    retry_base_delay_seconds: float = 0.5
    retry_max_delay_seconds: float = 30.0

    # ---- Freshness ---------------------------------------------------------
    # Requirement 4: strict 24h freshness window, with a small configurable
    # tolerance for "just published" server clock drift (not for genuinely
    # future-scheduled articles -- see MAX_CLOCK_SKEW_TOLERANCE_SECONDS below).
    freshness_window_hours: int = 24
    clock_skew_tolerance_seconds: int = 60

    # ---- Object storage (raw HTML) ----------------------------------------
    raw_storage_backend: Literal["local", "s3"] = "local"
    raw_storage_local_path: str = "./data/raw"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None

    @field_validator("llm_provider_order", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("clock_skew_tolerance_seconds")
    @classmethod
    def _clock_skew_tolerance_within_hard_cap(cls, v: int) -> int:
        """Requirement 4.10: this tolerance exists for minor server clock
        drift, not for accepting genuinely future-scheduled articles -- so
        it is capped, not just defaulted. A misconfigured value is rejected
        outright (fail fast) rather than silently clamped."""
        if v < 0:
            raise ValueError("clock_skew_tolerance_seconds must not be negative")
        if v > MAX_CLOCK_SKEW_TOLERANCE_SECONDS:
            raise ValueError(
                f"clock_skew_tolerance_seconds ({v}) exceeds the "
                f"{MAX_CLOCK_SKEW_TOLERANCE_SECONDS}s hard cap (Requirement 4.10)"
            )
        return v


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance."""
    return Settings()
