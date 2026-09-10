"""
HackerNews AI adapter (Phase 6, Task 6 -- design.md "HackerNewsAIAdapter",
requirements.md Requirement 12.1-12.3).

Discovers AI-related stories via the Algolia HN API, then fetches the
destination article page (NOT the HN comments page) for full-text extraction.

HN submission time is discovery metadata only. Freshness is measured against
explicit publication metadata on the destination article, never a repost time.

Anti-hallucination guarantee:
  - discover() yields only URLs explicitly present in the API response
  - Stories with null/missing ``url`` or ``title`` are skipped
  - parse() never fabricates article text or dates
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
import re

from pydantic import HttpUrl

from src.config.logging import get_logger
from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.crawlers.http import AsyncHttpClient, FetchResult
from src.extraction.articles import ArticleExtractor
from src.errors import ParsingError
from src.validation.news_dates import extract_news_publication

logger = get_logger(component="hackernews_adapter")


class HackerNewsAIAdapter(SourceAdapter):
    """Algolia HN API adapter for AI-related stories."""

    name = "hackernews_ai"
    vertical = "news"

    BASE_URL = "https://hn.algolia.com/api/v1/search_by_date"
    QUERY_PARAMS = {
        "tags": "story",
        # Algolia query is text, not a Boolean OR expression.
        "query": "AI",
        "hitsPerPage": 100,
    }

    def __init__(self, http_client: AsyncHttpClient, *, reference_time: datetime | None = None):
        super().__init__(http_client)
        self.reference_time = reference_time or datetime.now(timezone.utc)

    def _build_query_url(self) -> str:
        """Build the Algolia API query URL with AI-related search terms."""
        import urllib.parse

        cutoff = int((self.reference_time - timedelta(hours=24)).timestamp())
        params = urllib.parse.urlencode({
            **self.QUERY_PARAMS,
            "numericFilters": f"created_at_i>={cutoff},created_at_i<={int(self.reference_time.timestamp())}",
        })
        return f"{self.BASE_URL}?{params}"

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        """Query the Algolia API and yield DiscoveredUrl for each story.

        Stories with null/missing ``url`` or ``title`` are skipped
        (anti-hallucination: never invent article URLs).
        """
        query_url = self._build_query_url()
        fetch_result = await self.http_client.get(query_url)

        if fetch_result.status_code != 200:
            raise ParsingError(f"HTTP {fetch_result.status_code} from {query_url}")
        try:
            response_data = json.loads(fetch_result.text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ParsingError(f"Malformed Hacker News JSON from {query_url}") from exc

        if not isinstance(response_data, dict) or not isinstance(response_data.get("hits"), list):
            raise ParsingError(f"Missing/invalid Hacker News hits from {query_url}")
        hits = response_data["hits"]
        for hit in hits:
            if not isinstance(hit, dict):
                logger.warning("hackernews_story_skipped", reason="invalid_hit_shape")
                continue
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

            try:
                HttpUrl(article_url)
            except (ValueError, TypeError):
                continue
            if not isinstance(title, str) or not re.search(r"\b(?:AI|LLMs?|GPT|artificial intelligence|machine learning|deep learning|neural network)\b", title, re.I):
                continue

            yield DiscoveredUrl(
                url=article_url,
                metadata={
                    "discovery_url": query_url,
                    "hn_story_id": hit.get("objectID"),
                    "hn_title": title,
                    "hn_created_at": hit.get("created_at"),
                    "hn_points": hit.get("points"),
                    "hn_num_comments": hit.get("num_comments"),
                },
            )

    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        """Fetch the destination article page (not HN comments page)."""
        result = await self.http_client.get(discovered.url)
        if result.status_code != 200:
            raise ParsingError(f"HTTP {result.status_code} from {discovered.url}")
        return result

    async def parse(
        self, fetch_result: FetchResult, discovered: DiscoveredUrl
    ) -> list[ParsedRecord]:
        """Extract full text and the destination article publication timestamp.

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

        # HN can submit an old article today: never use its submission as publication.
        published_at, date_candidates = extract_news_publication(html)
        date_candidates["hn_created_at"] = discovered.metadata.get("hn_created_at")

        # --- Build extracted_metadata (Requirement 17) ---
        truncated = len(extracted_text) > ArticleExtractor.MAX_CONTENT_LENGTH
        extracted_metadata: dict = {
            "source_url": source_url,
            "discovery_url": discovered.metadata.get("discovery_url"),
            "hn_story_id": discovered.metadata.get("hn_story_id"),
            "hn_created_at": discovered.metadata.get("hn_created_at"),
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
            "publication_date_candidates": date_candidates,
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
