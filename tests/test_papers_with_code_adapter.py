from __future__ import annotations

from pathlib import Path

import pytest

from src.crawlers.base import DiscoveredUrl
from src.crawlers.http import FetchResult
from src.crawlers.papers_with_code import PapersWithCodeAdapter
from src.errors import ParsingError

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "papers_with_code_sample_response.json"


@pytest.fixture
def sample_response() -> str:
    return FIXTURE_PATH.read_text()


@pytest.mark.asyncio
async def test_parse_skips_entry_missing_title(sample_response):
    """The fixture has 3 entries; one has an empty title and no url_abs
    substitute -- it must be rejected, not guessed (Section 48)."""
    adapter = PapersWithCodeAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=sample_response, content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert len(records) == 2


@pytest.mark.asyncio
async def test_parse_extracts_title_authors_url(sample_response):
    adapter = PapersWithCodeAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=sample_response, content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    paper1 = records[0].data
    assert paper1["title"] == "Efficient Sparse Attention for Long-Context Transformers"
    assert paper1["authors"] == ["Jane Doe", "John Smith"]
    assert paper1["paper_url"] == "https://paperswithcode.com/paper/efficient-sparse-attention-for-long"


@pytest.mark.asyncio
async def test_parse_extracts_repository_relation_as_github_url(sample_response):
    adapter = PapersWithCodeAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=sample_response, content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert records[0].data["github_url"] == "https://github.com/example-lab/sparse-attention"
    # github_stars is never set by the adapter itself, only by enrichment
    assert records[0].data["github_stars"] is None


@pytest.mark.asyncio
async def test_parse_paper_with_null_repository_has_none_github_url(sample_response):
    adapter = PapersWithCodeAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=sample_response, content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert records[1].data["github_url"] is None


@pytest.mark.asyncio
async def test_parse_extracts_published_date(sample_response):
    from datetime import datetime, timezone

    adapter = PapersWithCodeAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=sample_response, content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert records[0].data["published_date"] == datetime(2026, 8, 9, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_parse_malformed_json_raises_parsing_error():
    adapter = PapersWithCodeAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text="{not valid json", content_hash="x", headers={})
    with pytest.raises(ParsingError):
        await adapter.parse(fetch_result, DiscoveredUrl(url="x"))


@pytest.mark.asyncio
async def test_parse_non_github_repository_url_rejected():
    """If PWC ever links a non-github repo host, it must not be passed
    through as a github_url (would break GitHub enrichment downstream)."""
    import json

    payload = {
        "results": [
            {
                "id": "x",
                "title": "GitLab-hosted paper",
                "url_abs": "https://paperswithcode.com/paper/x",
                "published": "2026-08-01",
                "authors": [],
                "repository": {"url": "https://gitlab.com/foo/bar"},
            }
        ]
    }
    adapter = PapersWithCodeAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=json.dumps(payload), content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert records[0].data["github_url"] is None


@pytest.mark.asyncio
async def test_discover_paginates_correctly():
    adapter = PapersWithCodeAdapter(http_client=None, max_results=120, page_size=50)
    urls = [d async for d in adapter.discover()]
    assert len(urls) == 3  # ceil(120/50) = 3 pages
    assert "page=1" in urls[0].url
    assert "page=2" in urls[1].url
    assert "page=3" in urls[2].url
