from __future__ import annotations

from pathlib import Path

import pytest

from src.crawlers.arxiv import ArxivAdapter, extract_github_url_from_text
from src.crawlers.base import DiscoveredUrl
from src.crawlers.http import FetchResult

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "arxiv_sample_response.xml"


@pytest.fixture
def sample_atom_feed() -> str:
    return FIXTURE_PATH.read_text()


def test_extract_github_url_from_text_finds_valid_url():
    text = "Code available at https://github.com/example-lab/sparse-attention for reproducibility."
    assert extract_github_url_from_text(text) == "https://github.com/example-lab/sparse-attention"


def test_extract_github_url_from_text_none_when_absent():
    assert extract_github_url_from_text("No code released for this paper.") is None


def test_extract_github_url_strips_trailing_punctuation():
    text = "See (https://github.com/foo/bar)."
    assert extract_github_url_from_text(text) == "https://github.com/foo/bar"


@pytest.mark.asyncio
async def test_parse_extracts_all_entries(sample_atom_feed):
    adapter = ArxivAdapter(http_client=None)
    fetch_result = FetchResult(url="https://export.arxiv.org/api/query", status_code=200, text=sample_atom_feed, content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="https://export.arxiv.org/api/query"))
    assert len(records) == 3


@pytest.mark.asyncio
async def test_parse_extracts_title_authors_and_url(sample_atom_feed):
    adapter = ArxivAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=sample_atom_feed, content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))

    paper1 = records[0].data
    assert paper1["title"] == "Efficient Sparse Attention for Long-Context Transformers"
    assert paper1["authors"] == ["Jane Doe", "John Smith"]
    assert paper1["paper_url"] == "http://arxiv.org/abs/2601.01234v1"
    assert paper1["paper_external_id"] == "2601.01234v1"


@pytest.mark.asyncio
async def test_parse_finds_github_url_in_abstract(sample_atom_feed):
    adapter = ArxivAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=sample_atom_feed, content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert records[0].data["github_url"] == "https://github.com/example-lab/sparse-attention"
    # github_stars is never guessed by the adapter itself
    assert records[0].data["github_stars"] is None


@pytest.mark.asyncio
async def test_parse_paper_without_github_link_has_none(sample_atom_feed):
    adapter = ArxivAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=sample_atom_feed, content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert records[1].data["github_url"] is None


@pytest.mark.asyncio
async def test_parse_extracts_published_date(sample_atom_feed):
    from datetime import datetime, timezone

    adapter = ArxivAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=sample_atom_feed, content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert records[0].data["published_date"] == datetime(2026, 8, 9, 15, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_parse_malformed_xml_raises_parsing_error():
    from src.errors import ParsingError

    adapter = ArxivAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text="<not><valid", content_hash="x", headers={})
    with pytest.raises(ParsingError):
        await adapter.parse(fetch_result, DiscoveredUrl(url="x"))


@pytest.mark.asyncio
async def test_discover_paginates_by_page_size():
    adapter = ArxivAdapter(http_client=None, max_results=120, page_size=50)
    urls = [d async for d in adapter.discover()]
    assert len(urls) == 3  # 0-50, 50-100, 100-120
    assert "start=0" in urls[0].url
    assert "start=50" in urls[1].url
    assert "start=100" in urls[2].url
