"""
Dashboard backend configuration.

The dashboard is a *read-only viewer* on top of the existing ingestion
database, so it deliberately reuses the pipeline's own configuration object
(``src.config.settings.Settings``) for the database URL instead of defining a
second, competing source of truth. Nothing here is hardcoded to a credential:
``DATABASE_URL`` always comes from the environment (or a local ``.env``).

The only dashboard-specific settings are the CORS origins for local frontend
development, which are intentionally *not* wildcarded.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from src.config.settings import get_settings as get_pipeline_settings

# Local Next.js dev server defaults. Overridable via DASHBOARD_CORS_ORIGINS
# (comma separated) so a developer on a different port does not have to edit
# code. We never fall back to "*" -- an unbounded origin list is not something
# this app should ever ship with.
DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)

# Pagination guard rails: the API must never be able to pull an entire table
# into memory, no matter what the caller asks for.
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def _parse_origins(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_CORS_ORIGINS
    origins = tuple(item.strip() for item in raw.split(",") if item.strip())
    return origins or DEFAULT_CORS_ORIGINS


@dataclass(frozen=True)
class DashboardSettings:
    """Dashboard-only settings; DB configuration is delegated to the pipeline."""

    cors_origins: tuple[str, ...] = field(default=DEFAULT_CORS_ORIGINS)
    default_page_size: int = DEFAULT_PAGE_SIZE
    max_page_size: int = MAX_PAGE_SIZE

    @property
    def database_url(self) -> str:
        """Resolved from the environment via the pipeline's Settings object."""
        return get_pipeline_settings().database_url


@lru_cache
def get_dashboard_settings() -> DashboardSettings:
    return DashboardSettings(
        cors_origins=_parse_origins(os.getenv("DASHBOARD_CORS_ORIGINS")),
    )
