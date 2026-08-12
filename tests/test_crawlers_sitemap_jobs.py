from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.crawlers.base import DiscoveredUrl
from src.crawlers.http import FetchResult
from src.crawlers.sitemap_jobs_base import SitemapJobsAdapter
from src.crawlers.wellfound import WellfoundAIAdapter
from src.crawlers.builtin import BuiltInAIAdapter


class MockHttpClient:
    def __init__(self, responses: dict, status_codes: dict = None):
        self.responses = responses
        self.status_codes = status_codes or {}

    async def get(self, url: str) -> FetchResult:
        status = self.status_codes.get(url, 200 if url in self.responses else 404)
        text = self.responses.get(url, "Not Found")
        return FetchResult(url=url, status_code=status, text=text, content_hash="x", headers={})


@pytest.fixture
def sitemap_xml_valid():
    return """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/job/1</loc>
  </url>
  <url>
    <loc>https://example.com/job/2</loc>
  </url>
  <url>
    <loc>  https://example.com/job/2  </loc> <!-- Duplicate -->
  </url>
</urlset>
"""

@pytest.fixture
def sitemap_xml_empty():
    return """<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>"""

@pytest.fixture
def sitemap_xml_malformed():
    return "Not XML"

@pytest.fixture
def html_valid_job():
    return """<html>
<head>
<script type="application/ld+json">
{
  "@context": "http://schema.org",
  "@type": "JobPosting",
  "title": "Software Engineer",
  "datePosted": "2026-08-01T00:00:00Z",
  "url": "https://example.com/job/1",
  "hiringOrganization": {
    "@type": "Organization",
    "name": "Acme Corp"
  }
}
</script>
</head>
<body></body></html>"""

@pytest.fixture
def html_missing_required():
    return """<html>
<head>
<script type="application/ld+json">
{
  "@context": "http://schema.org",
  "@type": "JobPosting",
  "title": "Software Engineer"
}
</script>
</head></html>"""

@pytest.fixture
def html_no_jobposting():
    return """<html>
<head>
<script type="application/ld+json">
{
  "@context": "http://schema.org",
  "@type": "Organization",
  "name": "Acme Corp"
}
</script>
</head></html>"""

@pytest.fixture
def html_graph_job():
    return """<html>
<head>
<script type="application/ld+json">
{
  "@context": "http://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "name": "Acme Corp"
    },
    {
      "@type": "JobPosting",
      "title": "Data Scientist",
      "datePosted": "2026-08-02",
      "hiringOrganization": {
        "name": "Beta Corp"
      }
    }
  ]
}
</script>
</head></html>"""

@pytest.fixture
def html_malformed_jsonld():
    return """<html>
<head>
<script type="application/ld+json">
{bad json}
</script>
</head></html>"""

@pytest.fixture
def html_multiple_blocks():
    return """<html>
<head>
<script type="application/ld+json">
{bad json}
</script>
<script type="application/ld+json">
{
  "@type": "JobPosting",
  "title": "DevOps",
  "datePosted": "2026-08-03",
  "hiringOrganization": {"name": "Gamma Corp"}
}
</script>
</head></html>"""

# --- Sitemap Base Tests ---

@pytest.mark.asyncio
async def test_sitemap_discover_valid(sitemap_xml_valid):
    client = MockHttpClient({"https://wellfound.com/sitemap.xml": sitemap_xml_valid})
    adapter = WellfoundAIAdapter(http_client=client)
    
    urls = [d async for d in adapter.discover()]
    assert len(urls) == 2
    assert urls[0].url == "https://example.com/job/1"
    assert urls[1].url == "https://example.com/job/2"

@pytest.mark.asyncio
async def test_sitemap_discover_empty(sitemap_xml_empty):
    client = MockHttpClient({"https://wellfound.com/sitemap.xml": sitemap_xml_empty})
    adapter = WellfoundAIAdapter(http_client=client)
    
    urls = [d async for d in adapter.discover()]
    assert len(urls) == 0

@pytest.mark.asyncio
async def test_sitemap_discover_malformed(sitemap_xml_malformed):
    client = MockHttpClient({"https://wellfound.com/sitemap.xml": sitemap_xml_malformed})
    adapter = WellfoundAIAdapter(http_client=client)
    
    urls = [d async for d in adapter.discover()]
    assert len(urls) == 0

@pytest.mark.asyncio
async def test_sitemap_discover_http_failure():
    client = MockHttpClient({}, status_codes={"https://wellfound.com/sitemap.xml": 500})
    adapter = WellfoundAIAdapter(http_client=client)
    
    urls = [d async for d in adapter.discover()]
    assert len(urls) == 0

# --- Parsing Tests (Wellfound/BuiltIn) ---

@pytest.mark.asyncio
async def test_parse_valid_job(html_valid_job):
    adapter = WellfoundAIAdapter(http_client=None)
    fetch_result = FetchResult(url="https://example.com/job/1", status_code=200, text=html_valid_job, content_hash="x", headers={})
    
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="https://example.com/job/1"))
    assert len(records) == 1
    job = records[0].data
    assert job["title"] == "Software Engineer"
    assert job["company"] == "Acme Corp"
    assert job["url"] == "https://example.com/job/1"
    assert job["posted_at"] == datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)

@pytest.mark.asyncio
async def test_parse_missing_required(html_missing_required):
    adapter = BuiltInAIAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=html_missing_required, content_hash="x", headers={})
    
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert len(records) == 0

@pytest.mark.asyncio
async def test_parse_no_jobposting(html_no_jobposting):
    adapter = WellfoundAIAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=html_no_jobposting, content_hash="x", headers={})
    
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert len(records) == 0

@pytest.mark.asyncio
async def test_parse_graph_job(html_graph_job):
    adapter = BuiltInAIAdapter(http_client=None)
    fetch_result = FetchResult(url="https://example.com/x", status_code=200, text=html_graph_job, content_hash="x", headers={})
    
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="https://example.com/x"))
    assert len(records) == 1
    job = records[0].data
    assert job["title"] == "Data Scientist"
    assert job["company"] == "Beta Corp"
    # url fallback to discovered url
    assert job["url"] == "https://example.com/x"
    assert job["posted_at"] == datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc)

@pytest.mark.asyncio
async def test_parse_malformed_jsonld(html_malformed_jsonld):
    adapter = WellfoundAIAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=html_malformed_jsonld, content_hash="x", headers={})
    
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert len(records) == 0

@pytest.mark.asyncio
async def test_parse_multiple_blocks(html_multiple_blocks):
    adapter = BuiltInAIAdapter(http_client=None)
    fetch_result = FetchResult(url="https://foo.com/j", status_code=200, text=html_multiple_blocks, content_hash="x", headers={})
    
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="https://foo.com/j"))
    assert len(records) == 1
    assert records[0].data["title"] == "DevOps"

@pytest.mark.asyncio
async def test_parse_empty_page():
    adapter = WellfoundAIAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text="", content_hash="x", headers={})
    
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert len(records) == 0
