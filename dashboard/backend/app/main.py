"""
GraphOne dashboard API.

A read-only FastAPI surface over the existing ingestion database. It creates
no tables, writes no rows, and reuses the pipeline's ORM models and engine
factory so the two can never disagree about the schema.

Run locally:
    uvicorn dashboard.backend.app.main:app --reload --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dashboard.backend.app.config import get_dashboard_settings
from dashboard.backend.app.routers import entities, health, records, stats
from src.storage.database import dispose_engine, init_engine

API_PREFIX = "/api"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the engine once at startup (DATABASE_URL is read from the
    # environment by the pipeline Settings object) and dispose the pool on
    # shutdown so reloads do not leak connections.
    init_engine()
    try:
        yield
    finally:
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_dashboard_settings()

    app = FastAPI(
        title="GraphOne Intelligence Dashboard API",
        version="0.1.0",
        description="Read-only API over the GraphOne ingestion database.",
        lifespan=lifespan,
    )

    # Local frontend development only -- an explicit origin list, never "*".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(stats.router, prefix=API_PREFIX)
    app.include_router(records.router, prefix=API_PREFIX)
    app.include_router(entities.router, prefix=API_PREFIX)
    return app


app = create_app()
