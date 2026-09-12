"""Liveness/readiness endpoint.

Returns 200 in both the healthy and the degraded case: the frontend renders a
"backend connection" badge and needs to distinguish "API up, DB down" from
"API unreachable". A non-200 would collapse those two states into one.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.backend.app.db import get_session
from dashboard.backend.app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="API and database health")
async def health(session: AsyncSession = Depends(get_session)) -> HealthResponse:
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:  # pragma: no cover - env dependent
        return HealthResponse(
            status="degraded",
            database="unavailable",
            detail=type(exc).__name__,
        )
    return HealthResponse(status="ok", database="connected")
