"""Bounded read-only details and recent ingestion activity; no inferred links."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.backend.app.db import get_session
from dashboard.backend.app.pagination import timestamp_expression
from dashboard.backend.app.schemas import (
    ActivityItem, ActivityResponse, EntityDetail, EntityRef, JobDetail, JobOut,
    NewsDetail, ProductDetail, ProductOut, ProvenanceOut, ResearchPaperDetail,
    StartupDetail, StartupOut,
)
from src.storage.models import (
    CanonicalEntity, EntityAlias, Job, News, Product, RawDocument, ResearchPaper, Startup,
)

router = APIRouter(tags=["details"])


async def require_record(session, model, record_id):
    record = await session.get(model, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


async def record_detail(session, model, schema, record_id):
    record = await require_record(session, model, record_id)
    detail = schema.model_validate(record)
    if record.raw_document_id:
        raw = await session.get(RawDocument, record.raw_document_id)
        if raw is not None:
            detail.provenance = ProvenanceOut.model_validate(raw)
    entity_id = getattr(record, "canonical_entity_id", None)
    if entity_id:
        entity = await session.get(CanonicalEntity, entity_id)
        if entity is not None:
            detail.canonical_entity = EntityRef.model_validate(entity)
    return detail


@router.get("/startups/{record_id}", response_model=StartupDetail)
async def startup_detail(record_id: UUID, session: AsyncSession = Depends(get_session)):
    return await record_detail(session, Startup, StartupDetail, record_id)


@router.get("/products/{record_id}", response_model=ProductDetail)
async def product_detail(record_id: UUID, session: AsyncSession = Depends(get_session)):
    return await record_detail(session, Product, ProductDetail, record_id)


@router.get("/research-papers/{record_id}", response_model=ResearchPaperDetail)
async def research_detail(record_id: UUID, session: AsyncSession = Depends(get_session)):
    return await record_detail(session, ResearchPaper, ResearchPaperDetail, record_id)


@router.get("/news/{record_id}", response_model=NewsDetail)
async def news_detail(record_id: UUID, session: AsyncSession = Depends(get_session)):
    return await record_detail(session, News, NewsDetail, record_id)


@router.get("/jobs/{record_id}", response_model=JobDetail)
async def job_detail(record_id: UUID, session: AsyncSession = Depends(get_session)):
    return await record_detail(session, Job, JobDetail, record_id)


@router.get("/entities/{record_id}", response_model=EntityDetail)
async def entity_detail(
    record_id: UUID,
    relationship_limit: int = Query(
        20, ge=1, le=20,
        description="Maximum linked records per type and alias preview size (1–20).",
    ),
    session: AsyncSession = Depends(get_session),
):
    entity = await require_record(session, CanonicalEntity, record_id)
    detail = EntityDetail(
        **EntityRef.model_validate(entity).model_dump(), relationship_limit=relationship_limit,
    )
    for model, schema, field, count_field in (
        (Startup, StartupOut, "startups", "startup_count"),
        (Product, ProductOut, "products", "product_count"),
        (Job, JobOut, "jobs", "job_count"),
    ):
        clause = model.canonical_entity_id == record_id
        count = await session.scalar(select(func.count()).select_from(model).where(clause)) or 0
        rows = (await session.scalars(
            select(model).where(clause).order_by(
                timestamp_expression(session, model.collected_at).desc().nulls_last(), model.id.asc(),
            ).limit(relationship_limit)
        )).all()
        setattr(detail, field, [schema.model_validate(row) for row in rows])
        setattr(detail, count_field, count)
    alias_clause = EntityAlias.canonical_entity_id == record_id
    detail.alias_count = await session.scalar(
        select(func.count()).select_from(EntityAlias).where(alias_clause)
    ) or 0
    detail.aliases = list((await session.scalars(
        select(EntityAlias.alias).where(alias_clause)
        .order_by(EntityAlias.alias.asc(), EntityAlias.id.asc()).limit(relationship_limit)
    )).all())
    detail.total_records = detail.startup_count + detail.product_count + detail.job_count
    return detail


def utc_timestamp(value: datetime | None) -> datetime | None:
    """The pipeline's naive timestamps mean UTC; never compare naive and aware."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def activity_key(item: ActivityItem):
    stamp = utc_timestamp(item.collected_at)
    # NULL is last in descending order; real equal timestamps have a stable key.
    return (stamp is not None, stamp or datetime.min.replace(tzinfo=timezone.utc), item.vertical, str(item.id))


@router.get("/dashboard/recent-activity", response_model=ActivityResponse)
async def recent_activity(
    limit: int = Query(10, ge=1, le=50), session: AsyncSession = Depends(get_session),
):
    items = []
    # Top K per vertical is sufficient for global top K. At most 5 * 50 rows
    # are materialized, using narrow projections instead of metadata blobs.
    for model, vertical, title, url in (
        (Startup, "startups", Startup.entity_name, Startup.source_url),
        (Product, "products", func.coalesce(Product.product_name, Product.startup_name), Product.source_url),
        (ResearchPaper, "research-papers", ResearchPaper.title, ResearchPaper.paper_url),
        (News, "news", News.title, News.url),
        (Job, "jobs", Job.title, Job.url),
    ):
        rows = (await session.execute(
            select(model.id, title, model.source_name, url, model.collected_at).order_by(
                timestamp_expression(session, model.collected_at).desc().nulls_last(), model.id.desc(),
            ).limit(limit)
        )).all()
        items.extend(ActivityItem(
            id=row[0], vertical=vertical, title=row[1], source_name=row[2],
            source_url=row[3], collected_at=utc_timestamp(row[4]),
        ) for row in rows)
    return ActivityResponse(items=sorted(items, key=activity_key, reverse=True)[:limit], limit=limit)
