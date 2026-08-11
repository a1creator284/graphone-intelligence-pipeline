"""
Article text extraction (Phase 6, Task 2 -- design.md "Article Extractor
Component", requirements.md Requirement 2 and Requirement 21).

Extracts the main body text from a fetched article HTML page, discarding
navigation, ads, and other boilerplate. Two libraries are attempted in a
strict priority order:

  1. trafilatura (primary) -- generally the most accurate for news-style
     pages and preserves paragraph structure.
  2. newspaper3k (fallback) -- attempted only when trafilatura returns
     nothing usable.

Anti-hallucination guarantee (Requirements 2.5, 11, 19)
--------------------------------------------------------
This module NEVER invents, summarizes, or reconstructs article text, and
never substitutes an RSS description or the article title for the real
body. It only returns text that trafilatura or newspaper3k actually pulled
out of the HTML it was given. If neither library can produce at least
MIN_CONTENT_LENGTH characters of real content, `extract_text()` returns
None. Callers MUST treat a None return as an extraction failure per
Requirement 2.6 / 21.3: do not persist a News record, do not fall back to
fabricated or partial content.

Out of scope for this module (belongs to later tasks): full-text storage
location / persistence (Task 5, the RSS adapter base class), freshness
filtering (Task 3), and any retry/backoff (already centralized in
src/crawlers/http.py -- this module never retries).
"""
from __future__ import annotations

import trafilatura
from newspaper import Article as NewspaperArticle

from src.config.logging import get_logger

logger = get_logger(component="article_extractor")


class ArticleExtractor:
    """Stateless HTML-in / body-text-out extraction utility.

    Usage:
        text = ArticleExtractor.extract_text(html, source_url)
        if text is None:
            # extraction failed -- reject the record, never fabricate text
            ...
    """

    MIN_CONTENT_LENGTH = 100
    MAX_CONTENT_LENGTH = 100_000

    @staticmethod
    def extract_text(html: str, source_url: str) -> str | None:
        """Extract article body text from `html`.

        Returns the extracted text (preserving paragraph structure and
        whitespace, truncated to MAX_CONTENT_LENGTH characters if needed),
        or None if extraction fails or produces fewer than
        MIN_CONTENT_LENGTH real characters.

        Never raises: extraction-library errors are caught and logged, and
        result in a fallback attempt (or an eventual None), not a crash --
        one bad article must not take down the pipeline.
        """
        if not html or not html.strip():
            logger.warning("extraction_failed", url=source_url, library=None, reason="empty_html")
            return None

        text, library = ArticleExtractor._extract_with_trafilatura(html)
        if text is None:
            text, library = ArticleExtractor._extract_with_newspaper(html, source_url)

        if text is None:
            logger.warning(
                "extraction_failed",
                url=source_url,
                library="trafilatura+newspaper3k",
                reason="both_extractors_returned_insufficient_content",
            )
            return None

        truncated = len(text) > ArticleExtractor.MAX_CONTENT_LENGTH
        if truncated:
            logger.warning(
                "extraction_truncated",
                url=source_url,
                library=library,
                original_chars=len(text),
                max_chars=ArticleExtractor.MAX_CONTENT_LENGTH,
            )
            text = text[: ArticleExtractor.MAX_CONTENT_LENGTH]

        logger.info("extraction_success", url=source_url, library=library, chars=len(text), truncated=truncated)
        return text

    @staticmethod
    def _extract_with_trafilatura(html: str) -> tuple[str | None, str | None]:
        try:
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=False,
                no_fallback=False,
            )
        except Exception as exc:  # noqa: BLE001 -- library failure, not a pipeline crash
            logger.warning("extraction_library_error", library="trafilatura", error=str(exc))
            return None, None

        if text and len(text.strip()) >= ArticleExtractor.MIN_CONTENT_LENGTH:
            return text, "trafilatura"
        return None, None

    @staticmethod
    def _extract_with_newspaper(html: str, source_url: str) -> tuple[str | None, str | None]:
        try:
            article = NewspaperArticle(source_url)
            article.set_html(html)
            article.parse()
            text = article.text
        except Exception as exc:  # noqa: BLE001 -- library failure, not a pipeline crash
            logger.warning("extraction_library_error", library="newspaper3k", error=str(exc))
            return None, None

        if text and len(text.strip()) >= ArticleExtractor.MIN_CONTENT_LENGTH:
            return text, "newspaper3k"
        return None, None
