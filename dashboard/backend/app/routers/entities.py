"""
Canonical entity explorer (read-only).

Entity resolution is the part of the pipeline whose output is hardest to
eyeball from raw tables: a ``canonical_entities`` row is just a name, and the
interesting question is always "what did it actually connect?". So this
endpoint returns each entity *with* the number of startups, products and jobs
that resolved onto it, plus its aliases.

Implementation notes:

* The per-entity counts are correlated scalar subqueries evaluated only for
  the rows on the current page (Postgres and SQLite both push the LIMIT down
  before evaluating them), so the cost scales with page size, not table size.
* Aliases are fetched in a single follow-up ``IN (...)`` query against the
  page's ids rather than per row, which keeps this at two round trips instead
  of N+1.
* Everything here is a SELECT. No route writes, and no route can be coaxed
  into returning an unbounded slice.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.backend.app.db import get_session
from dashboard.backend.app.pagination import PageParams, page_params
from dashboard.backend.app.schemas import CanonicalEntityOut, Page
from src.storage.models import CanonicalEntity, EntityAlias, Job, Product, Startup

router = APIRouter(tags=["entities"])

# Sort keys the UI exposes. Whitelisted on purpose: the sort column is never
# taken from user input directly.
SORT_KEYS = ("records", "name", "recent")


def _link_count(model: Any) -> Any:
    """Correlated ``count(*)`` of rows on ``model`` pointing at the entity."""
    return (
        select(func.count())
        .select_from(model)
        .where(model.canonical_entity_id == CanonicalEntity.id)
        .correlate(CanonicalEntity)
        .scalar_subquery()
    )


def _escaped_like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get(
    "/entities",
    response_model=Page[CanonicalEntityOut],
    summary="List canonical entities with linked-record counts",
)
async def list_entities(
    params: PageParams = Depends(page_params),
    sort: str = Query(
        "records",
        description="records = most connected first, name = A-Z, recent = newest first.",
    ),
    session: AsyncSession = Depends(get_session),
) -> Page[CanonicalEntityOut]:
    if sort not in SORT_KEYS:
        sort = "records"

    startup_count = _link_count(Startup).label("startup_count")
    product_count = _link_count(Product).label("product_count")
    job_count = _link_count(Job).label("job_count")
    alias_count = _link_count_aliases().label("alias_count")
    total_records = (startup_count + product_count + job_count).label("total_records")

    stmt = select(
        CanonicalEntity,
        startup_count,
        product_count,
        job_count,
        alias_count,
        total_records,
    )
    count_stmt = select(func.count()).select_from(CanonicalEntity)

    if params.q:
        pattern = _escaped_like(params.q)
        # Match the display name, the normalized form, or any alias -- looking
        # an entity up by a name the pipeline folded away is the common case.
        alias_match = (
            select(EntityAlias.id)
            .where(
                EntityAlias.canonical_entity_id == CanonicalEntity.id,
                EntityAlias.alias.ilike(pattern, escape="\\"),
            )
            .correlate(CanonicalEntity)
            .exists()
        )
        clause = or_(
            CanonicalEntity.canonical_name.ilike(pattern, escape="\\"),
            CanonicalEntity.normalized_name.ilike(pattern, escape="\\"),
            alias_match,
        )
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)

    if sort == "name":
        stmt = stmt.order_by(CanonicalEntity.canonical_name.asc())
    elif sort == "recent":
        stmt = stmt.order_by(CanonicalEntity.created_at.desc(), CanonicalEntity.canonical_name.asc())
    else:
        stmt = stmt.order_by(total_records.desc(), CanonicalEntity.canonical_name.asc())

    total = await session.scalar(count_stmt) or 0

    rows = (
        await session.execute(stmt.limit(params.limit).offset(params.offset))
    ).all()

    # Second round trip: aliases for just this page.
    entity_ids = [row[0].id for row in rows]
    aliases_by_entity: dict[UUID, list[str]] = defaultdict(list)
    if entity_ids:
        alias_rows = (
            await session.execute(
                select(EntityAlias.canonical_entity_id, EntityAlias.alias)
                .where(EntityAlias.canonical_entity_id.in_(entity_ids))
                .order_by(EntityAlias.alias.asc())
            )
        ).all()
        for entity_id, alias in alias_rows:
            aliases_by_entity[entity_id].append(alias)

    items = [
        CanonicalEntityOut(
            id=entity.id,
            canonical_name=entity.canonical_name,
            normalized_name=entity.normalized_name,
            entity_type=entity.entity_type,
            created_at=entity.created_at,
            alias_count=aliases or 0,
            startup_count=startups or 0,
            product_count=products or 0,
            job_count=jobs or 0,
            total_records=totals or 0,
            aliases=aliases_by_entity.get(entity.id, []),
        )
        for entity, startups, products, jobs, aliases, totals in rows
    ]

    return Page[CanonicalEntityOut](
        items=items,
        total=total,
        limit=params.limit,
        offset=params.offset,
        has_more=params.offset + len(items) < total,
    )


def _link_count_aliases() -> Any:
    """Alias count uses a different FK name, so it gets its own helper."""
    return (
        select(func.count())
        .select_from(EntityAlias)
        .where(EntityAlias.canonical_entity_id == CanonicalEntity.id)
        .correlate(CanonicalEntity)
        .scalar_subquery()
    )
