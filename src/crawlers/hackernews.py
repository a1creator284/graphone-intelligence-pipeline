"""
HackerNews AI adapter (Phase 6, Task 6 -- design.md "HackerNewsAIAdapter",
requirements.md Requirement 12.1-12.3).

Discovers AI-related stories via the Algolia HN API, then fetches the
destination article page (NOT the HN comments page) for full-text extraction.

Key design decision (Requirement 12.3): uses HN story ``created_at`` as the
publication date, not the destination article's date, since freshness is
measured against HN submission time.

Anti-hallucination guarantee:
  - discover() yields only URLs explicitly present in the API response
  - Stories with null/missing ``url`` or ``title`` are skipped
  - parse() never fabricates article text or dates
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from src.config.logging import get_logger
from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.crawlers.http import AsyncHttpClient, FetchResult
from src.extraction.articles import ArticleExtractor
from src.extraction.dates import extract_publication_date, parse_absolute_date

logger = get_logger(component="hackernews_adapter")


class HackerNewsAIAdapter(SourceAdapter):
    """Algolia HN API adapter for AI-related stories."""

    name = "hackernews_ai"
    vertical = "news"

    BASE_URL = "https://hn.algolia.com/api/v1/search_by_date"
    QUERY_PARAMS = {
        "tags": "story",
        "query": (
            '"artificial intelligence" OR "machine learning" OR '
            '"deep learning" OR "neural network" OR "LLM" OR "GPT"'
        ),
        "hitsPerPage": 50,
    }

    def __init__(self, http_client: AsyncHttpClient, *, reference_time: datetime | None = None):
        super().__init__(http_client)
        self.reference_time = reference_time or datetime.now(timezone.utc)

    def _build_query_url(self) -> str:
        """Build the Algolia API query URL with AI-related search terms."""
        import urllib.parse

        params = urllib.parse.urlencode(self.QUERY_PARAMS)
        return f"{self.BASE_URL}?{params}"

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        """Query the Algolia API and yield DiscoveredUrl for each story.

        Stories with null/missing ``url`` or ``title`` are skipped
        (anti-hallucination: never invent article URLs).
        """
        query_url = self._build_query_url()
        fetch_result = await self.http_client.get(query_url)

        try:
            response_data = json.loads(fetch_result.text)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("hackernews_json_parse_failed", error=str(exc), url=query_url)
            return

        hits = response_data.get("hits", [])
        for hit in hits:
            article_url = hit.get("url")
            title = hit.get("title")

            # Skip stories without a destination URL or title
            if not article_url or not title:
                logger.debug(
                    "hackernews_story_skipped",
                    object_id=hit.get("objectID"),
                    reason="missing_url_or_title",
                )
                continue

            yield DiscoveredUrl(
                url=article_url,
                metadata={
                    "hn_story_id": hit.get("objectID"),
                    "hn_title": title,
                    "hn_created_at": hit.get("created_at"),
                    "hn_points": hit.get("points"),
                    "hn_num_comments": hit.get("num_comments"),
                },
            )

    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        """Fetch the destination article page (not HN comments page)."""
        return await self.http_client.get(discovered.url)

    async def parse(
        self, fetch_result: FetchResult, discovered: DiscoveredUrl
    ) -> list[ParsedRecord]:
        """Extract full text from the article and use HN created_at as date.

        Returns a list with one ParsedRecord on success, or an empty list
        when extraction fails.
        """
        html = fetch_result.text
        source_url = discovered.url

        # --- Full-text extraction (Requirement 2) ---
        extracted_text = ArticleExtractor.extract_text(html, source_url)
        if extracted_text is None:
            logger.warning(
                "extraction_failed",
                url=source_url,
                source=self.name,
                bytes_fetched=len(html) if html else 0,
                extraction_library="trafilatura+newspaper3k",
                reason="no_content_or_too_short",
            )
            return []

        # --- Publication date (Requirement 12.3) ---
        # Use HN story created_at as structured_value (highest priority).
        hn_created_at = discovered.metadata.get("hn_created_at")
        published_at = extract_publication_date(
            html=html,
            structured_value=hn_created_at,
            reference_time=self.reference_time,
        )

        # --- Build extracted_metadata (Requirement 17) ---
        truncated = len(extracted_text) > ArticleExtractor.MAX_CONTENT_LENGTH
        extracted_metadata: dict = {
            "full_text": extracted_text,
            "truncated": truncated,
            "extraction_library": "trafilatura",
        }

        # --- Construct ParsedRecord ---
        title = discovered.metadata.get("hn_title") or ""
        data = {
            "title": title,
            "url": source_url,
            "source_name": self.name,
            "published_at": published_at,
            "full_text_location": "inline:extracted_metadata.full_text",
            "extracted_metadata": extracted_metadata,
            "publication_date_candidates": {
                "hn_created_at": hn_created_at,
                "selected": "hn_created_at" if hn_created_at and published_at else None,
            },
        }

        return [
            ParsedRecord(
                record_type="NEWS",
                data=data,
                source_name=self.name,
                source_url=source_url,
                fetch_result=fetch_result,
            )
        ]
