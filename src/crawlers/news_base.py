"""
Base RSS news adapter (Phase 6, Task 5 -- design.md "RSS News Adapters",
requirements.md Requirements 2, 3, 12).

Provides shared discover/fetch/parse logic for all RSS-based news sources
(TechCrunch, The Verge, MIT Tech Review, Synced Review). Concrete
subclasses only need to set ``name``, ``feed_url``, and ``vertical``.

Anti-hallucination guarantee (Requirements 11, 19):
  - discover() yields only URLs explicitly present in the RSS feed
  - parse() never fabricates title, URL, date, or article text
  - extraction failure → extraction_failed metadata, empty record list
  - missing date → published_at=None (validation gate rejects downstream)
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import feedparser

from src.config.logging import get_logger
from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.crawlers.http import AsyncHttpClient, FetchResult
from src.extraction.articles import ArticleExtractor
from src.extraction.dates import extract_publication_date, to_utc

logger = get_logger(component="rss_news_adapter")


class RSSNewsAdapter(SourceAdapter):
    """Abstract base for RSS-feed-based news adapters.

    Subclasses MUST set:
      - ``name``: adapter identifier matching SOURCE_REGISTRY (e.g. "techcrunch_ai_rss")
      - ``feed_url``: full URL of the RSS feed
      - ``vertical``: always "news" for this base
    """

    name: str  # set by subclass
    vertical: str = "news"
    feed_url: str  # set by subclass

    def __init__(self, http_client: AsyncHttpClient, *, reference_time: datetime | None = None):
        super().__init__(http_client)
        self.reference_time = reference_time or datetime.now(timezone.utc)

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        """Fetch and parse the RSS feed, yielding one DiscoveredUrl per item.

        Each DiscoveredUrl carries RSS metadata (title, pubDate, description)
        so that parse() can use pubDate as the structured_value for the
        DateEngine priority chain.
        """
        fetch_result = await self.http_client.get(self.feed_url)
        feed = feedparser.parse(fetch_result.text)

        for entry in feed.entries:
            link = entry.get("link")
            if not link:
                continue

            title = entry.get("title")
            pub_date = entry.get("published")
            description = entry.get("description") or entry.get("summary")

            yield DiscoveredUrl(
                url=link,
                metadata={
                    "rss_title": title,
                    "rss_pubDate": pub_date,
                    "rss_description": description,
                },
            )

    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        """Fetch the article page HTML for full-text extraction."""
        return await self.http_client.get(discovered.url)

    async def parse(
        self, fetch_result: FetchResult, discovered: DiscoveredUrl
    ) -> list[ParsedRecord]:
        """Extract full text and publication date from the fetched article.

        Returns a list with one ParsedRecord on success, or an empty list
        when extraction fails (anti-hallucination: never fabricate text).
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

        # --- Publication date extraction (Requirement 3) ---
        rss_pub_date = discovered.metadata.get("rss_pubDate")
        published_at = extract_publication_date(
            html=html,
            structured_value=rss_pub_date,
            reference_time=self.reference_time,
        )

        # --- Build extracted_metadata (Requirement 17) ---
        truncated = len(extracted_text) > ArticleExtractor.MAX_CONTENT_LENGTH
        extracted_metadata: dict = {
            "full_text": extracted_text,
            "truncated": truncated,
            "extraction_library": "trafilatura",  # primary; fallback noted in ArticleExtractor logs
        }

        # --- Construct ParsedRecord ---
        title = discovered.metadata.get("rss_title") or ""
        data = {
            "title": title,
            "url": source_url,
            "source_name": self.name,
            "published_at": published_at,
            "full_text_location": "inline:extracted_metadata.full_text",
            "extracted_metadata": extracted_metadata,
            "publication_date_candidates": {
                "rss_pubDate": rss_pub_date,
                "selected": "rss_pubDate" if rss_pub_date and published_at else None,
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
