"""
Read-only list endpoints for each vertical.

All five routes share the same bounded offset/limit pagination helper, so no
endpoint can be coaxed into loading a full table. Ordering is newest-first on
the most meaningful timestamp for each record type, which is also what the
frontend tables want by default.

Each route also accepts an optional ``?q=`` substring filter. The searchable
columns are declared per route rather than inferred, so a future schema
column (an internal note, a raw blob) cannot become searchable by accident.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.backend.app.db import get_session
from dashboard.backend.app.pagination import PageParams, page_params, paginate
from dashboard.backend.app.schemas import (
    JobOut,
    NewsOut,
    Page,
    ProductOut,
    ResearchPaperOut,
    StartupOut,
)
from src.storage.models import Job, News, Product, ResearchPaper, Startup

router = APIRouter(tags=["records"])


@router.get("/startups", response_model=Page[StartupOut], summary="List startups")
async def list_startups(
    params: PageParams = Depends(page_params),
    session: AsyncSession = Depends(get_session),
) -> Page[StartupOut]:
    return await paginate(
        session,
        Startup,
        StartupOut,
        params,
        Startup.collected_at.desc(),
        search_columns=(Startup.entity_name, Startup.source_name),
    )


@router.get("/products", response_model=Page[ProductOut], summary="List products")
async def list_products(
    params: PageParams = Depends(page_params),
    session: AsyncSession = Depends(get_session),
) -> Page[ProductOut]:
    return await paginate(
        session,
        Product,
        ProductOut,
        params,
        Product.collected_at.desc(),
        search_columns=(Product.product_name, Product.startup_name, Product.source_name),
    )


@router.get(
    "/research-papers",
    response_model=Page[ResearchPaperOut],
    summary="List research papers",
)
async def list_research_papers(
    params: PageParams = Depends(page_params),
    session: AsyncSession = Depends(get_session),
) -> Page[ResearchPaperOut]:
    return await paginate(
        session,
        ResearchPaper,
        ResearchPaperOut,
        params,
        ResearchPaper.collected_at.desc(),
        search_columns=(ResearchPaper.title, ResearchPaper.source_name),
    )


@router.get("/news", response_model=Page[NewsOut], summary="List news articles")
async def list_news(
    params: PageParams = Depends(page_params),
    session: AsyncSession = Depends(get_session),
) -> Page[NewsOut]:
    return await paginate(
        session,
        News,
        NewsOut,
        params,
        News.published_at.desc(),
        search_columns=(News.title, News.source_name),
    )


@router.get("/jobs", response_model=Page[JobOut], summary="List job postings")
async def list_jobs(
    params: PageParams = Depends(page_params),
    session: AsyncSession = Depends(get_session),
) -> Page[JobOut]:
    return await paginate(
        session,
        Job,
        JobOut,
        params,
        Job.posted_at.desc(),
        search_columns=(Job.title, Job.company, Job.source_name),
    )
