"""
TechCrunch AI RSS adapter (Phase 6, Task 7.1 -- requirements.md Requirement 12.4).

Minimal RSS adapter subclass for TechCrunch's AI category feed. All
discover/fetch/parse logic is inherited from RSSNewsAdapter.
"""
from __future__ import annotations

from src.crawlers.news_base import RSSNewsAdapter


class TechCrunchAIAdapter(RSSNewsAdapter):
    """TechCrunch AI category RSS feed adapter."""

    name = "techcrunch_ai_rss"
    feed_url = "https://techcrunch.com/category/artificial-intelligence/feed/"
