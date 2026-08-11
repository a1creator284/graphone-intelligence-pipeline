"""
Synced Review RSS adapter (Phase 6, Task 7.4 -- requirements.md Requirement 12.7).

Minimal RSS adapter subclass for Synced Review's main feed. All
discover/fetch/parse logic is inherited from RSSNewsAdapter.

Note: Synced Review has a lower publication frequency compared to TechCrunch
and The Verge. The feed may occasionally include sponsored content which is
not filtered automatically.
"""
from __future__ import annotations

from src.crawlers.news_base import RSSNewsAdapter


class SyncedReviewAdapter(RSSNewsAdapter):
    """Synced Review RSS feed adapter."""

    name = "synced_review_rss"
    feed_url = "https://syncedreview.com/feed/"
