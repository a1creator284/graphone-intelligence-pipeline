"""
Tests for RSS-based news adapters (Phase 6, Task 7.5 -- requirements.md
Requirement 16.1, 16.2).

Validates that TechCrunch, The Verge, MIT Tech Review, and Synced Review
adapters correctly:
  - Parse RSS feeds from mocked XML fixtures
  - Extract article URLs and metadata
  - Use RSS pubDate as structured_value for date extraction
  - Extract full text from article pages
  - Reject records when extraction fails

All tests use mocked fixtures (Requirement 18.1) -- no live network access.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.crawlers.base import DiscoveredUrl
from src.crawlers.http import AsyncHttpClient, FetchResult
from src.crawlers.mitreview import MITTechReviewAIAdapter
from src.crawlers.synced import SyncedReviewAdapter
from src.crawlers.techcrunch import TechCrunchAIAdapter
from src.crawlers.theverge import TheVergeAIAdapter

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def techcrunch_rss_feed() -> str:
    return (FIXTURES_DIR / "techcrunch_ai_sample.xml").read_text()


@pytest.fixture
def theverge_rss_feed() -> str:
    return (FIXTURES_DIR / "theverge_ai_sample.xml").read_text()


@pytest.fixture
def mitreview_rss_feed() -> str:
    return (FIXTURES_DIR / "mitreview_ai_sample.xml").read_text()


@pytest.fixture
def synced_rss_feed() -> str:
    return (FIXTURES_DIR / "synced_sample.xml").read_text()


@pytest.fixture
def sample_article_techcrunch() -> str:
    return (FIXTURES_DIR / "sample_article_techcrunch.html").read_text()


@pytest.fixture
def sample_article_verge() -> str:
    return (FIXTURES_DIR / "sample_article_verge.html").read_text()


@pytest.fixture
def sample_article_mit() -> str:
    return (FIXTURES_DIR / "sample_article_mit.html").read_text()


@pytest.fixture
def reference_time() -> datetime:
    """Fixed reference time for deterministic tests."""
    return datetime(2026, 8, 10, 16, 0, 0, tzinfo=timezone.utc)


# ============================================================================
# TechCrunch Adapter Tests
# ============================================================================


@pytest.mark.asyncio
async def test_techcrunch_adapter_parses_rss_feed(techcrunch_rss_feed):
    """Verify TechCrunch adapter discovers article URLs from RSS feed."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    mock_client.get.return_value = FetchResult(
        url="https://techcrunch.com/category/artificial-intelligence/feed/",
        status_code=200,
        text=techcrunch_rss_feed,
        content_hash="abc123",
        headers={},
    )

    adapter = TechCrunchAIAdapter(http_client=mock_client)
    discovered = [d async for d in adapter.discover()]

    assert len(discovered) == 3
    assert discovered[0].url == "https://techcrunch.com/2026/08/10/startup-raises-100m-enterprise-ai/"
    assert discovered[0].metadata["rss_title"] == "Startup Raises $100M for Enterprise AI Platform"
    assert discovered[0].metadata["rss_pubDate"] == "Mon, 10 Aug 2026 14:30:00 +0000"


@pytest.mark.asyncio
async def test_techcrunch_adapter_extracts_metadata_from_rss(techcrunch_rss_feed):
    """Verify TechCrunch adapter extracts title, pubDate, and description."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    mock_client.get.return_value = FetchResult(
        url="https://techcrunch.com/category/artificial-intelligence/feed/",
        status_code=200,
        text=techcrunch_rss_feed,
        content_hash="abc123",
        headers={},
    )

    adapter = TechCrunchAIAdapter(http_client=mock_client)
    discovered = [d async for d in adapter.discover()]

    first_item = discovered[0]
    assert first_item.metadata["rss_title"] == "Startup Raises $100M for Enterprise AI Platform"
    assert first_item.metadata["rss_description"] == "A San Francisco-based startup has raised $100 million in Series B funding for its enterprise AI platform."
    assert first_item.metadata["rss_pubDate"] == "Mon, 10 Aug 2026 14:30:00 +0000"


@pytest.mark.asyncio
async def test_techcrunch_adapter_extracts_full_text_from_article(
    reference_time, sample_article_techcrunch
):
    """Verify TechCrunch adapter extracts full text and uses RSS pubDate."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    adapter = TechCrunchAIAdapter(http_client=mock_client, reference_time=reference_time)

    discovered = DiscoveredUrl(
        url="https://techcrunch.com/2026/08/10/startup-raises-100m-enterprise-ai/",
        metadata={
            "rss_title": "Startup Raises $100M for Enterprise AI Platform",
            "rss_pubDate": "Mon, 10 Aug 2026 14:30:00 +0000",
            "rss_description": "A San Francisco-based startup...",
        },
    )

    fetch_result = FetchResult(
        url=discovered.url,
        status_code=200,
        text=sample_article_techcrunch,
        content_hash="def456",
        headers={},
    )

    records = await adapter.parse(fetch_result, discovered)

    assert len(records) == 1
    record = records[0]
    assert record.data["source_name"] == "techcrunch_ai_rss"
    assert record.data["title"] == "Startup Raises $100M for Enterprise AI Platform"
    assert record.data["url"] == discovered.url
    assert record.data["full_text_location"] == "inline:extracted_metadata.full_text"
    assert "extracted_metadata" in record.data
    assert "full_text" in record.data["extracted_metadata"]
    assert len(record.data["extracted_metadata"]["full_text"]) > 100


@pytest.mark.asyncio
async def test_techcrunch_adapter_uses_rss_pubdate_as_structured_value(
    reference_time, sample_article_techcrunch
):
    """Verify TechCrunch adapter prioritizes RSS pubDate for date extraction."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    adapter = TechCrunchAIAdapter(http_client=mock_client, reference_time=reference_time)

    discovered = DiscoveredUrl(
        url="https://techcrunch.com/2026/08/10/test/",
        metadata={
            "rss_title": "Test Article",
            "rss_pubDate": "Mon, 10 Aug 2026 14:30:00 +0000",
        },
    )

    fetch_result = FetchResult(
        url=discovered.url,
        status_code=200,
        text=sample_article_techcrunch,
        content_hash="xyz789",
        headers={},
    )

    records = await adapter.parse(fetch_result, discovered)

    assert len(records) == 1
    assert records[0].data["published_at"] is not None
    # Verify the publication_date_candidates shows RSS pubDate was used
    assert "publication_date_candidates" in records[0].data
    assert records[0].data["publication_date_candidates"]["rss_pubDate"] == "Mon, 10 Aug 2026 14:30:00 +0000"


@pytest.mark.asyncio
async def test_techcrunch_adapter_rejects_when_extraction_fails(reference_time):
    """Verify TechCrunch adapter returns empty list when full-text extraction fails."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    adapter = TechCrunchAIAdapter(http_client=mock_client, reference_time=reference_time)

    discovered = DiscoveredUrl(
        url="https://techcrunch.com/2026/08/10/test/",
        metadata={"rss_title": "Test", "rss_pubDate": "Mon, 10 Aug 2026 14:30:00 +0000"},
    )

    # HTML with no extractable content (too short)
    fetch_result = FetchResult(
        url=discovered.url,
        status_code=200,
        text="<html><body><p>Short.</p></body></html>",
        content_hash="short",
        headers={},
    )

    records = await adapter.parse(fetch_result, discovered)

    # Extraction failure -> empty list (anti-hallucination)
    assert len(records) == 0


# ============================================================================
# The Verge Adapter Tests
# ============================================================================


@pytest.mark.asyncio
async def test_theverge_adapter_parses_rss_feed(theverge_rss_feed):
    """Verify The Verge adapter discovers article URLs from RSS feed."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    mock_client.get.return_value = FetchResult(
        url="https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        status_code=200,
        text=theverge_rss_feed,
        content_hash="abc123",
        headers={},
    )

    adapter = TheVergeAIAdapter(http_client=mock_client)
    discovered = [d async for d in adapter.discover()]

    assert len(discovered) == 3
    assert discovered[0].url == "https://www.theverge.com/2026/8/10/24150101/microsoft-windows-next-gen-ai-assistant-integration"
    assert discovered[0].metadata["rss_title"] == "Microsoft Integrates Next-Gen AI Assistant Across Windows Platform"


@pytest.mark.asyncio
async def test_theverge_adapter_extracts_full_text_from_article(
    reference_time, sample_article_verge
):
    """Verify The Verge adapter extracts full text and uses RSS pubDate."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    adapter = TheVergeAIAdapter(http_client=mock_client, reference_time=reference_time)

    discovered = DiscoveredUrl(
        url="https://www.theverge.com/2026/8/10/24150101/microsoft-windows-next-gen-ai-assistant-integration",
        metadata={
            "rss_title": "Microsoft Integrates Next-Gen AI Assistant Across Windows Platform",
            "rss_pubDate": "Mon, 10 Aug 2026 15:00:00 +0000",
        },
    )

    fetch_result = FetchResult(
        url=discovered.url,
        status_code=200,
        text=sample_article_verge,
        content_hash="verge456",
        headers={},
    )

    records = await adapter.parse(fetch_result, discovered)

    assert len(records) == 1
    record = records[0]
    assert record.data["source_name"] == "theverge_ai_rss"
    assert record.data["title"] == "Microsoft Integrates Next-Gen AI Assistant Across Windows Platform"
    assert "full_text" in record.data["extracted_metadata"]
    assert len(record.data["extracted_metadata"]["full_text"]) > 100


@pytest.mark.asyncio
async def test_theverge_adapter_uses_rss_pubdate(reference_time, sample_article_verge):
    """Verify The Verge adapter prioritizes RSS pubDate for date extraction."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    adapter = TheVergeAIAdapter(http_client=mock_client, reference_time=reference_time)

    discovered = DiscoveredUrl(
        url="https://www.theverge.com/2026/8/10/test",
        metadata={
            "rss_title": "Test",
            "rss_pubDate": "Mon, 10 Aug 2026 15:00:00 +0000",
        },
    )

    fetch_result = FetchResult(
        url=discovered.url,
        status_code=200,
        text=sample_article_verge,
        content_hash="xyz",
        headers={},
    )

    records = await adapter.parse(fetch_result, discovered)

    assert len(records) == 1
    assert records[0].data["publication_date_candidates"]["rss_pubDate"] == "Mon, 10 Aug 2026 15:00:00 +0000"


@pytest.mark.asyncio
async def test_theverge_adapter_rejects_when_extraction_fails(reference_time):
    """Verify The Verge adapter returns empty list when full-text extraction fails."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    adapter = TheVergeAIAdapter(http_client=mock_client, reference_time=reference_time)

    discovered = DiscoveredUrl(
        url="https://www.theverge.com/test",
        metadata={"rss_title": "Test", "rss_pubDate": "Mon, 10 Aug 2026 15:00:00 +0000"},
    )

    fetch_result = FetchResult(
        url=discovered.url,
        status_code=200,
        text="<html><body></body></html>",  # Empty content
        content_hash="empty",
        headers={},
    )

    records = await adapter.parse(fetch_result, discovered)
    assert len(records) == 0


# ============================================================================
# MIT Technology Review Adapter Tests
# ============================================================================


@pytest.mark.asyncio
async def test_mitreview_adapter_parses_rss_feed(mitreview_rss_feed):
    """Verify MIT Tech Review adapter discovers article URLs from RSS feed."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    mock_client.get.return_value = FetchResult(
        url="https://www.technologyreview.com/topic/artificial-intelligence/feed",
        status_code=200,
        text=mitreview_rss_feed,
        content_hash="mit123",
        headers={},
    )

    adapter = MITTechReviewAIAdapter(http_client=mock_client)
    discovered = [d async for d in adapter.discover()]

    assert len(discovered) == 3
    assert discovered[0].url == "https://www.technologyreview.com/2026/08/10/1098123/neuromorphic-computing-ai-energy-crisis/"
    assert discovered[0].metadata["rss_title"] == "Why Neuromorphic Computing Could Solve AI's Massive Energy Crisis"


@pytest.mark.asyncio
async def test_mitreview_adapter_extracts_full_text_from_article(
    reference_time, sample_article_mit
):
    """Verify MIT Tech Review adapter extracts full text and uses RSS pubDate."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    adapter = MITTechReviewAIAdapter(http_client=mock_client, reference_time=reference_time)

    discovered = DiscoveredUrl(
        url="https://www.technologyreview.com/2026/08/10/1098123/neuromorphic-computing-ai-energy-crisis/",
        metadata={
            "rss_title": "Why Neuromorphic Computing Could Solve AI's Massive Energy Crisis",
            "rss_pubDate": "Mon, 10 Aug 2026 13:45:00 +0000",
        },
    )

    fetch_result = FetchResult(
        url=discovered.url,
        status_code=200,
        text=sample_article_mit,
        content_hash="mit456",
        headers={},
    )

    records = await adapter.parse(fetch_result, discovered)

    assert len(records) == 1
    record = records[0]
    assert record.data["source_name"] == "mit_technology_review_ai_rss"
    assert record.data["title"] == "Why Neuromorphic Computing Could Solve AI's Massive Energy Crisis"
    assert "full_text" in record.data["extracted_metadata"]
    assert len(record.data["extracted_metadata"]["full_text"]) > 100


@pytest.mark.asyncio
async def test_mitreview_adapter_uses_rss_pubdate(reference_time, sample_article_mit):
    """Verify MIT Tech Review adapter prioritizes RSS pubDate for date extraction."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    adapter = MITTechReviewAIAdapter(http_client=mock_client, reference_time=reference_time)

    discovered = DiscoveredUrl(
        url="https://www.technologyreview.com/test",
        metadata={
            "rss_title": "Test",
            "rss_pubDate": "Mon, 10 Aug 2026 13:45:00 +0000",
        },
    )

    fetch_result = FetchResult(
        url=discovered.url,
        status_code=200,
        text=sample_article_mit,
        content_hash="xyz",
        headers={},
    )

    records = await adapter.parse(fetch_result, discovered)

    assert len(records) == 1
    assert records[0].data["publication_date_candidates"]["rss_pubDate"] == "Mon, 10 Aug 2026 13:45:00 +0000"


@pytest.mark.asyncio
async def test_mitreview_adapter_rejects_when_extraction_fails(reference_time):
    """Verify MIT Tech Review adapter returns empty list when extraction fails."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    adapter = MITTechReviewAIAdapter(http_client=mock_client, reference_time=reference_time)

    discovered = DiscoveredUrl(
        url="https://www.technologyreview.com/test",
        metadata={"rss_title": "Test", "rss_pubDate": "Mon, 10 Aug 2026 13:45:00 +0000"},
    )

    fetch_result = FetchResult(
        url=discovered.url,
        status_code=200,
        text="<html><body><nav>Menu</nav></body></html>",  # No article content
        content_hash="empty",
        headers={},
    )

    records = await adapter.parse(fetch_result, discovered)
    assert len(records) == 0


# ============================================================================
# Synced Review Adapter Tests
# ============================================================================


@pytest.mark.asyncio
async def test_synced_adapter_parses_rss_feed(synced_rss_feed):
    """Verify Synced Review adapter discovers article URLs from RSS feed."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    mock_client.get.return_value = FetchResult(
        url="https://syncedreview.com/feed/",
        status_code=200,
        text=synced_rss_feed,
        content_hash="synced123",
        headers={},
    )

    adapter = SyncedReviewAdapter(http_client=mock_client)
    discovered = [d async for d in adapter.discover()]

    assert len(discovered) == 3
    assert discovered[0].url == "https://syncedreview.com/2026/08/10/stanford-google-propose-moe-vision-transformers/"
    assert discovered[0].metadata["rss_title"] == "Stanford & Google Propose Mixture-of-Experts Architecture for Vision Transformers"


@pytest.mark.asyncio
async def test_synced_adapter_extracts_full_text_from_article(
    reference_time, sample_article_mit
):
    """Verify Synced Review adapter extracts full text and uses RSS pubDate."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    adapter = SyncedReviewAdapter(http_client=mock_client, reference_time=reference_time)

    discovered = DiscoveredUrl(
        url="https://syncedreview.com/2026/08/10/stanford-google-propose-moe-vision-transformers/",
        metadata={
            "rss_title": "Stanford & Google Propose Mixture-of-Experts Architecture for Vision Transformers",
            "rss_pubDate": "Mon, 10 Aug 2026 16:00:00 +0000",
        },
    )

    # Reusing MIT article fixture for full-text extraction test
    fetch_result = FetchResult(
        url=discovered.url,
        status_code=200,
        text=sample_article_mit,
        content_hash="synced456",
        headers={},
    )

    records = await adapter.parse(fetch_result, discovered)

    assert len(records) == 1
    record = records[0]
    assert record.data["source_name"] == "synced_review_rss"
    assert record.data["title"] == "Stanford & Google Propose Mixture-of-Experts Architecture for Vision Transformers"
    assert "full_text" in record.data["extracted_metadata"]
    assert len(record.data["extracted_metadata"]["full_text"]) > 100


@pytest.mark.asyncio
async def test_synced_adapter_uses_rss_pubdate(reference_time, sample_article_mit):
    """Verify Synced Review adapter prioritizes RSS pubDate for date extraction."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    adapter = SyncedReviewAdapter(http_client=mock_client, reference_time=reference_time)

    discovered = DiscoveredUrl(
        url="https://syncedreview.com/test",
        metadata={
            "rss_title": "Test",
            "rss_pubDate": "Mon, 10 Aug 2026 16:00:00 +0000",
        },
    )

    fetch_result = FetchResult(
        url=discovered.url,
        status_code=200,
        text=sample_article_mit,
        content_hash="xyz",
        headers={},
    )

    records = await adapter.parse(fetch_result, discovered)

    assert len(records) == 1
    assert records[0].data["publication_date_candidates"]["rss_pubDate"] == "Mon, 10 Aug 2026 16:00:00 +0000"


@pytest.mark.asyncio
async def test_synced_adapter_rejects_when_extraction_fails(reference_time):
    """Verify Synced Review adapter returns empty list when extraction fails."""
    mock_client = AsyncMock(spec=AsyncHttpClient)
    adapter = SyncedReviewAdapter(http_client=mock_client, reference_time=reference_time)

    discovered = DiscoveredUrl(
        url="https://syncedreview.com/test",
        metadata={"rss_title": "Test", "rss_pubDate": "Mon, 10 Aug 2026 16:00:00 +0000"},
    )

    fetch_result = FetchResult(
        url=discovered.url,
        status_code=200,
        text="<html><body><header>Header only</header></body></html>",
        content_hash="empty",
        headers={},
    )

    records = await adapter.parse(fetch_result, discovered)
    assert len(records) == 0


# ============================================================================
# Cross-Adapter Validation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_all_adapters_set_correct_source_name():
    """Verify each adapter sets the correct source_name matching SOURCE_REGISTRY."""
    mock_client = AsyncMock(spec=AsyncHttpClient)

    tc_adapter = TechCrunchAIAdapter(http_client=mock_client)
    assert tc_adapter.name == "techcrunch_ai_rss"

    verge_adapter = TheVergeAIAdapter(http_client=mock_client)
    assert verge_adapter.name == "theverge_ai_rss"

    mit_adapter = MITTechReviewAIAdapter(http_client=mock_client)
    assert mit_adapter.name == "mit_technology_review_ai_rss"

    synced_adapter = SyncedReviewAdapter(http_client=mock_client)
    assert synced_adapter.name == "synced_review_rss"


@pytest.mark.asyncio
async def test_all_adapters_set_correct_feed_url():
    """Verify each adapter sets the correct RSS feed URL."""
    mock_client = AsyncMock(spec=AsyncHttpClient)

    tc_adapter = TechCrunchAIAdapter(http_client=mock_client)
    assert tc_adapter.feed_url == "https://techcrunch.com/category/artificial-intelligence/feed/"

    verge_adapter = TheVergeAIAdapter(http_client=mock_client)
    assert verge_adapter.feed_url == "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"

    mit_adapter = MITTechReviewAIAdapter(http_client=mock_client)
    assert mit_adapter.feed_url == "https://www.technologyreview.com/topic/artificial-intelligence/feed"

    synced_adapter = SyncedReviewAdapter(http_client=mock_client)
    assert synced_adapter.feed_url == "https://syncedreview.com/feed/"


@pytest.mark.asyncio
async def test_all_adapters_set_vertical_to_news():
    """Verify all RSS adapters set vertical to 'news'."""
    mock_client = AsyncMock(spec=AsyncHttpClient)

    for adapter_class in [TechCrunchAIAdapter, TheVergeAIAdapter, MITTechReviewAIAdapter, SyncedReviewAdapter]:
        adapter = adapter_class(http_client=mock_client)
        assert adapter.vertical == "news"
