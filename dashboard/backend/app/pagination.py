"""
Shared pagination helper.

Every list endpoint runs a bounded ``SELECT ... LIMIT/OFFSET`` plus a
``SELECT count(*)``; nothing ever materialises a whole table. ``limit`` is
hard-capped by FastAPI validation (``le=MAX_PAGE_SIZE``) so an oversized
request is rejected at the edge rather than being silently clamped deep in a
query builder.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.backend.app.config import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from dashboard.backend.app.schemas import Page

SchemaT = TypeVar("SchemaT")


MAX_SEARCH_LENGTH = 120


@dataclass(frozen=True)
class PageParams:
    limit: int
    offset: int
    q: str | None = None
    sort: str = "recent"


def page_params(
    sort: str = Query("recent", max_length=100),
    limit: int = Query(
        DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Rows per page (max {MAX_PAGE_SIZE}).",
    ),
    offset: int = Query(0, ge=0, description="Rows to skip."),
    q: str | None = Query(
        None,
        max_length=MAX_SEARCH_LENGTH,
        description="Case-insensitive substring filter over the row's text columns.",
    ),
) -> PageParams:
    # Treat a whitespace-only query as "no filter" so an empty search box in
    # the UI does not turn into a LIKE '%%' that the planner has to think about.
    cleaned = q.strip() if q else None
    return PageParams(limit=limit, offset=offset, q=cleaned or None, sort=sort)


def _search_clause(columns: Sequence[Any], term: str) -> Any:
    """Build an OR of case-insensitive LIKEs across ``columns``.

    The term is passed as a bound parameter (``ilike`` builds one), so this is
    not string interpolation into SQL. ``%`` and ``_`` in user input are
    escaped so a search for "100%" cannot become a wildcard match.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    clauses = [col.ilike(pattern, escape="\\") for col in columns]
    if len(clauses) == 1:
        return clauses[0]
    from sqlalchemy import or_

    return or_(*clauses)


async def paginate(
    session: AsyncSession,
    model: Any,
    schema: type[SchemaT],
    params: PageParams,
    order_by: Any,
    search_columns: Sequence[Any] | None = None,
) -> Page[SchemaT]:
    """Return one bounded page of ``model`` rows mapped through ``schema``."""
    if params.sort not in {"recent", "oldest", "name", "source"}:
        raise HTTPException(status_code=422, detail="Unsupported sort key")
    count_stmt = select(func.count()).select_from(model)
    stmt = select(model)

    if params.q and search_columns:
        clause = _search_clause(search_columns, params.q)
        count_stmt = count_stmt.where(clause)
        stmt = stmt.where(clause)

    total = await session.scalar(count_stmt) or 0

    # Map keys to declared ORM expressions, never user-provided identifiers.
    if params.sort == "name" and search_columns:
        ordering = search_columns[0].asc().nulls_last()
    elif params.sort == "source":
        ordering = model.source_name.asc().nulls_last()
    else:
        timestamp = timestamp_expression(session, order_by.element)
        ordering = (timestamp.asc() if params.sort == "oldest" else timestamp.desc()).nulls_last()
    stmt = stmt.order_by(ordering, model.id.asc()).limit(params.limit).offset(params.offset)
    rows = (await session.execute(stmt)).scalars().all()

    items = [schema.model_validate(row) for row in rows]
    return Page[schema](  # type: ignore[valid-type]
        items=items,
        total=total,
        limit=params.limit,
        offset=params.offset,
        has_more=params.offset + len(items) < total,
    )


def timestamp_expression(session: AsyncSession, column: Any) -> Any:
    """SQLite stores datetimes as text; julianday normalizes offset-bearing values.

    Postgres timestamptz already orders by instant. Naive stored dates are UTC
    by the pipeline contract; NULL stays NULL and always sorts last.
    """
    return func.julianday(column) if session.get_bind().dialect.name == "sqlite" else column
