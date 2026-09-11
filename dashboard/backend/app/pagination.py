"""
Shared pagination helper.

Every list endpoint runs a bounded ``SELECT ... LIMIT/OFFSET`` plus a
``SELECT count(*)``; nothing ever materialises a whole table. ``limit`` is
hard-capped by FastAPI validation (``le=MAX_PAGE_SIZE``) so an oversized
request is rejected at the edge rather than being silently clamped deep in a
query builder.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.backend.app.config import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from dashboard.backend.app.schemas import Page

SchemaT = TypeVar("SchemaT")


@dataclass(frozen=True)
class PageParams:
    limit: int
    offset: int


def page_params(
    limit: int = Query(
        DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Rows per page (max {MAX_PAGE_SIZE}).",
    ),
    offset: int = Query(0, ge=0, description="Rows to skip."),
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


async def paginate(
    session: AsyncSession,
    model: Any,
    schema: type[SchemaT],
    params: PageParams,
    order_by: Any,
) -> Page[SchemaT]:
    """Return one bounded page of ``model`` rows mapped through ``schema``."""
    total = await session.scalar(select(func.count()).select_from(model)) or 0

    stmt = select(model).order_by(order_by).limit(params.limit).offset(params.offset)
    rows = (await session.execute(stmt)).scalars().all()

    items = [schema.model_validate(row) for row in rows]
    return Page[schema](  # type: ignore[valid-type]
        items=items,
        total=total,
        limit=params.limit,
        offset=params.offset,
        has_more=params.offset + len(items) < total,
    )
