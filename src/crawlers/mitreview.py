"""
MIT Technology Review AI RSS adapter (Phase 6, Task 7.3 -- requirements.md
Requirement 12.6).

Minimal RSS adapter subclass for MIT Technology Review's AI topic feed. All
discover/fetch/parse logic is inherited from RSSNewsAdapter.

Note (Requirement 18.2, 18.3): This endpoint requires verification against
live network traffic during deployment. The sandbox environment has restricted
egress and cannot reach this source directly.
"""
from __future__ import annotations

from src.crawlers.news_base import RSSNewsAdapter


class MITTechReviewAIAdapter(RSSNewsAdapter):
    """MIT Technology Review AI topic RSS feed adapter."""

    name = "mit_technology_review_ai_rss"
    feed_url = "https://www.technologyreview.com/topic/artificial-intelligence/feed"
