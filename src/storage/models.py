"""
SQLAlchemy 2.x ORM models.

Design notes:
- UUID primary keys everywhere (safe for distributed/concurrent workers,
  no auto-increment contention across processes).
- All timestamps are timezone-aware UTC.
- Uniqueness/dedup is enforced at the DATABASE level (unique constraints),
  never only in application code -- see Section 28.
- JSONB columns hold provider-specific / semi-structured metadata that
  doesn't warrant its own column.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


# JSONB is Postgres-only; for the sqlite test suite we fall back to JSON via
# a variant so the same models work in both engines without code branching.
from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator


class PortableJSONB(TypeDecorator):
    """JSONB on Postgres, JSON everywhere else (e.g. the sqlite test suite)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class PortableUUID(TypeDecorator):
    """Native UUID on Postgres, CHAR(36) string elsewhere."""

    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


def uuid_pk():
    return mapped_column(PortableUUID(), primary_key=True, default=new_uuid)


# ---------------------------------------------------------------------------
# Crawl bookkeeping
# ---------------------------------------------------------------------------


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    vertical: Mapped[str] = mapped_column(String(40), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    discovery_mechanism: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_sources_vertical", "vertical"),)


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    vertical: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|completed|failed
    target_records: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stats: Mapped[dict] = mapped_column(PortableJSONB(), default=dict)

    jobs: Mapped[list["CrawlJob"]] = relationship(back_populates="crawl_run")


class CrawlJob(Base):
    """A single unit of work (one URL/source page) within a crawl run."""

    __tablename__ = "crawl_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    crawl_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crawl_runs.id"), nullable=False)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|in_progress|done|failed|blocked
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    crawl_run: Mapped["CrawlRun"] = relationship(back_populates="jobs")

    __table_args__ = (
        # A given normalized URL should only be enqueued once per crawl run --
        # this is the DB-level guard against two workers racing on the same URL.
        UniqueConstraint("crawl_run_id", "normalized_url", name="uq_crawl_job_run_url"),
        Index("ix_crawl_jobs_status", "status"),
    )


class RawDocument(Base):
    """Raw provenance record for every fetched document (Section 8)."""

    __tablename__ = "raw_documents"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex
    raw_content_location: Mapped[str | None] = mapped_column(Text, nullable=True)  # local path or s3:// uri
    extraction_status: Mapped[str] = mapped_column(String(20), default="pending")
    publication_date_candidates: Mapped[dict] = mapped_column(PortableJSONB(), default=dict)
    crawl_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crawl_runs.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_raw_documents_content_hash"),
        Index("ix_raw_documents_canonical_url", "canonical_url"),
    )


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------


class CanonicalEntity(Base):
    __tablename__ = "canonical_entities"

    id: Mapped[uuid.UUID] = uuid_pk()
    canonical_name: Mapped[str] = mapped_column(String(250), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(250), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), default="company")  # company|other
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    aliases: Mapped[list["EntityAlias"]] = relationship(back_populates="canonical_entity")

    __table_args__ = (UniqueConstraint("normalized_name", name="uq_canonical_entities_normalized_name"),)


class EntityAlias(Base):
    __tablename__ = "entity_aliases"

    id: Mapped[uuid.UUID] = uuid_pk()
    canonical_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("canonical_entities.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(250), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(250), nullable=False)

    canonical_entity: Mapped["CanonicalEntity"] = relationship(back_populates="aliases")

    __table_args__ = (UniqueConstraint("normalized_alias", name="uq_entity_aliases_normalized_alias"),)


class EntityMappingLog(Base):
    """Full audit trail of every resolution decision (Section 22)."""

    __tablename__ = "entity_mapping_log"

    id: Mapped[uuid.UUID] = uuid_pk()
    raw_name: Mapped[str] = mapped_column(String(250), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(250), nullable=False)
    canonical_name: Mapped[str | None] = mapped_column(String(250), nullable=True)
    canonical_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("canonical_entities.id"), nullable=True)
    method: Mapped[str] = mapped_column(String(30), nullable=False)  # normalized_exact|alias|fuzzy|unresolved
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# Domain records
# ---------------------------------------------------------------------------


class Startup(Base):
    __tablename__ = "startups"

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_name: Mapped[str] = mapped_column(String(250), nullable=False)
    canonical_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("canonical_entities.id"), nullable=True)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("raw_documents.id"), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("source_name", "source_url", name="uq_startups_source_url"),
        CheckConstraint("employee_count IS NULL OR employee_count >= 0", name="ck_startups_employee_count_nonneg"),
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = uuid_pk()
    startup_name: Mapped[str] = mapped_column(String(250), nullable=False)
    canonical_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("canonical_entities.id"), nullable=True)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    pricing_model: Mapped[str | None] = mapped_column(
        Enum("FREE", "FREEMIUM", "PAID", "ENTERPRISE", name="pricing_model_enum"), nullable=True
    )
    raw_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("raw_documents.id"), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("source_name", "source_url", name="uq_products_source_url"),)


class ResearchPaper(Base):
    __tablename__ = "research_papers"

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[list] = mapped_column(PortableJSONB(), default=list)
    paper_url: Mapped[str] = mapped_column(Text, nullable=False)
    paper_external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)  # e.g. arXiv ID
    github_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    raw_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("raw_documents.id"), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("paper_url", name="uq_research_papers_paper_url"),
        CheckConstraint("github_stars IS NULL OR github_stars >= 0", name="ck_research_papers_stars_nonneg"),
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    company: Mapped[str] = mapped_column(String(250), nullable=False)
    canonical_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("canonical_entities.id"), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_remote: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    role_family: Mapped[str | None] = mapped_column(String(60), nullable=True)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(PortableJSONB(), default=dict)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("source_name", "url", name="uq_jobs_source_url"),)


class News(Base):
    __tablename__ = "news"

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    full_text_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_metadata: Mapped[dict] = mapped_column(PortableJSONB(), default=dict)
    raw_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("raw_documents.id"), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("source_name", "url", name="uq_news_source_url"),)


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class LLMRequest(Base):
    __tablename__ = "llm_requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    record_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    input_size_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_size_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # success|error
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    error_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_llm_requests_provider_status", "provider", "status"),)


class ProcessingError(Base):
    __tablename__ = "processing_errors"

    id: Mapped[uuid.UUID] = uuid_pk()
    crawl_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crawl_runs.id"), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_category: Mapped[str] = mapped_column(String(60), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict] = mapped_column(PortableJSONB(), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_processing_errors_category", "error_category"),)
