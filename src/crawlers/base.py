"""
Source adapter interface (Section 32).

Each concrete source (arxiv.py, news.py, jobs.py, ...) implements discover/
fetch/parse independently and is swappable without touching the worker pool
or the rest of the pipeline.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from src.crawlers.http import AsyncHttpClient, FetchResult


@dataclass(slots=True)
class DiscoveredUrl:
    url: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedRecord:
    """A parsed, not-yet-validated record plus its provenance."""

    record_type: str  # startup|product|research_paper|job|news
    data: dict[str, Any]
    source_name: str
    source_url: str
    fetch_result: FetchResult | None = None


class SourceAdapter(ABC):
    name: str
    vertical: str

    def __init__(self, http_client: AsyncHttpClient):
        self.http_client = http_client

    @abstractmethod
    def discover(self) -> AsyncIterator[DiscoveredUrl]:
        """Yield URLs (or API pages) worth fetching. Must be an async generator
        so discovery can be paginated without loading everything into memory."""
        raise NotImplementedError

    @abstractmethod
    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        """Fetch the raw content for one discovered item."""
        raise NotImplementedError

    @abstractmethod
    async def parse(self, fetch_result: FetchResult, discovered: DiscoveredUrl) -> list[ParsedRecord]:
        """Turn raw content into zero or more structured (unvalidated) records."""
        raise NotImplementedError
