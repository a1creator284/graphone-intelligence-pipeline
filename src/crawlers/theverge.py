"""
The Verge AI RSS adapter (Phase 6, Task 7.2 -- requirements.md Requirement 12.5).

Minimal RSS adapter subclass for The Verge's AI category feed. All
discover/fetch/parse logic is inherited from RSSNewsAdapter.
"""
from __future__ import annotations

from src.crawlers.news_base import RSSNewsAdapter


class TheVergeAIAdapter(RSSNewsAdapter):
    """The Verge AI category RSS feed adapter."""

    name = "theverge_ai_rss"
    feed_url = "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"
