"""
Record validation schemas for research papers and news articles.

This is the schema gate every parsed record must pass before being
persisted. It never repairs uncertain facts -- it either accepts a record
with nulls in place of unverifiable fields, or rejects it outright when a
truly required field is missing/invalid.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ResearchPaperRecord(BaseModel):
    schema_version: str = "1.0"
    record_type: str = "RESEARCH_PAPER"

    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    paper_url: str
    paper_external_id: str | None = None
    github_url: str | None = None
    github_stars: int | None = Field(default=None, ge=0)
    published_date: datetime | None = None
    source_name: str

    @field_validator("paper_url")
    @classmethod
    def _paper_url_must_be_http(cls, v: str) -> str:
        # Validate shape via HttpUrl without permanently changing the type
        # (HttpUrl round-trips can add trailing slashes we don't want).
        HttpUrl(v)
        return v

    @field_validator("github_url")
    @classmethod
    def _github_url_shape(cls, v: str | None) -> str | None:
        if v is None:
            return None
        from src.extraction.github import parse_github_repo_url

        if parse_github_repo_url(v) is None:
            raise ValueError(f"github_url is not a valid github.com/owner/repo URL: {v}")
        return v

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("title must not be blank/whitespace-only")
        return stripped


def validate_research_paper(data: dict) -> tuple[ResearchPaperRecord | None, str | None]:
    """Returns (validated_record, None) on success or (None, error_message)
    on failure. Never raises -- callers use the tuple to decide accept/reject.
    """
    try:
        return ResearchPaperRecord(**data), None
    except Exception as exc:  # noqa: BLE001 - surfaced as a rejection reason, not a crash
        return None, str(exc)


class NewsRecord(BaseModel):
    """News article record validation (Phase 6).

    Validates news articles before persistence, enforcing required fields
    and data quality standards per requirements.md Section 9.
    """

    schema_version: str = "1.0"
    record_type: str = "NEWS"

    title: str = Field(min_length=1)
    url: str
    source_name: str = Field(min_length=1)
    published_at: datetime
    full_text_location: str = Field(min_length=1)
    extracted_metadata: dict = Field(default_factory=dict)
    raw_document_id: str | None = None

    @field_validator("url")
    @classmethod
    def _url_must_be_http(cls, v: str) -> str:
        # Validate shape via HttpUrl without permanently changing the type
        HttpUrl(v)
        return v

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("title must not be blank/whitespace-only")
        return stripped

    @field_validator("source_name")
    @classmethod
    def _source_name_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("source_name must not be blank/whitespace-only")
        return stripped

    @field_validator("full_text_location")
    @classmethod
    def _full_text_location_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("full_text_location must not be blank/whitespace-only")
        return stripped

    @field_validator("published_at")
    @classmethod
    def _published_at_utc(cls, v: datetime) -> datetime:
        """Normalize published_at to UTC timezone-aware datetime."""
        if v.tzinfo is None:
            # Assume UTC for naive datetimes
            return v.replace(tzinfo=timezone.utc)
        # Convert to UTC if already timezone-aware
        return v.astimezone(timezone.utc)


def validate_news_record(data: dict) -> tuple[NewsRecord | None, str | None]:
    """Returns (validated_record, None) on success or (None, error_message)
    on failure. Never raises -- callers use the tuple to decide accept/reject.
    """
    try:
        return NewsRecord(**data), None
    except Exception as exc:  # noqa: BLE001 - surfaced as a rejection reason, not a crash
        return None, str(exc)


class JobRecord(BaseModel):
    """Job posting record validation (Phase 7)."""

    schema_version: str = "1.0"
    record_type: str = "JOB"

    title: str = Field(min_length=1)
    company: str = Field(min_length=1)
    url: str
    source_name: str = Field(min_length=1)
    posted_at: datetime
    is_remote: bool | None = None
    role_family: str | None = None
    raw_document_id: str | None = None
    metadata_json: dict = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _url_must_be_http(cls, v: str) -> str:
        HttpUrl(v)
        return v

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("title must not be blank/whitespace-only")
        return stripped

    @field_validator("company")
    @classmethod
    def _company_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("company must not be blank/whitespace-only")
        return stripped

    @field_validator("source_name")
    @classmethod
    def _source_name_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("source_name must not be blank/whitespace-only")
        return stripped

    @field_validator("posted_at")
    @classmethod
    def _posted_at_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


def validate_job_record(data: dict) -> tuple[JobRecord | None, str | None]:
    try:
        return JobRecord(**data), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
