"""
Response models for the dashboard API.

These are deliberately thin projections of the ORM models: the dashboard only
exposes the fields it renders, so adding a column to the pipeline schema never
silently leaks into the API surface.
"""
from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ItemT = TypeVar("ItemT")


class HealthResponse(BaseModel):
    status: str = Field(description="'ok' when the API can reach the database.")
    database: str = Field(description="'connected' or 'unavailable'.")
    detail: str | None = Field(
        default=None, description="Short diagnostic when the database is unreachable."
    )


class DashboardStats(BaseModel):
    """Row counts for each table the dashboard surfaces."""

    startups: int = 0
    products: int = 0
    research_papers: int = 0
    jobs: int = 0
    news: int = 0
    canonical_entities: int = 0
    raw_documents: int = 0


class StatsResponse(BaseModel):
    stats: DashboardStats
    generated_at: datetime


class Page(BaseModel, Generic[ItemT]):
    """Offset/limit page envelope shared by every list endpoint."""

    items: list[ItemT]
    total: int
    limit: int
    offset: int
    has_more: bool


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StartupOut(ORMModel):
    id: UUID
    entity_name: str
    source_name: str
    source_url: str
    employee_count: int | None = None
    collected_at: datetime | None = None


class ProductOut(ORMModel):
    id: UUID
    product_name: str | None = None
    startup_name: str
    source_name: str
    source_url: str
    pricing_model: str | None = None
    collected_at: datetime | None = None


class ResearchPaperOut(ORMModel):
    id: UUID
    title: str
    authors: list = Field(default_factory=list)
    paper_url: str
    github_url: str | None = None
    github_stars: int | None = None
    published_date: datetime | None = None
    source_name: str


class JobOut(ORMModel):
    id: UUID
    company: str
    title: str
    url: str
    posted_at: datetime | None = None
    is_remote: bool | None = None
    role_family: str | None = None
    source_name: str


class NewsOut(ORMModel):
    id: UUID
    title: str
    url: str
    source_name: str
    published_at: datetime | None = None
