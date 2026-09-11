"""Aggregate counts powering the dashboard homepage.

One ``SELECT count(*)`` per table, issued against the pipeline's own ORM
models -- no duplicated schema definitions and no table scans into Python.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.backend.app.db import get_session
from dashboard.backend.app.schemas import DashboardStats, StatsResponse
from src.storage.models import (
    CanonicalEntity,
    Job,
    News,
    Product,
    RawDocument,
    ResearchPaper,
    Startup,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Field name on DashboardStats -> ORM model to count.
_COUNTED_MODELS = {
    "startups": Startup,
    "products": Product,
    "research_papers": ResearchPaper,
    "jobs": Job,
    "news": News,
    "canonical_entities": CanonicalEntity,
    "raw_documents": RawDocument,
}


@router.get("/stats", response_model=StatsResponse, summary="Row counts per vertical")
async def dashboard_stats(session: AsyncSession = Depends(get_session)) -> StatsResponse:
    counts: dict[str, int] = {}
    for key, model in _COUNTED_MODELS.items():
        counts[key] = await session.scalar(select(func.count()).select_from(model)) or 0

    return StatsResponse(
        stats=DashboardStats(**counts),
        generated_at=datetime.now(timezone.utc),
    )
