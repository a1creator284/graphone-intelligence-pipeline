from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator

from bs4 import BeautifulSoup

from src.config.logging import get_logger
from src.crawlers.base import DiscoveredUrl, SourceAdapter
from src.crawlers.http import AsyncHttpClient
from src.errors import ParsingError

logger = get_logger(component="sitemap_jobs_base")


# Match whole terms, not incidental substrings such as "paid" or "email".
_AI_TERMS = re.compile(
    r"\b(?:AI|ML|LLMs?|MLOps|artificial intelligence|machine learning|deep learning|"
    r"generative AI|natural language processing|NLP|computer vision|data scien(?:ce|tist)s?)\b",
    re.IGNORECASE,
)


def is_ai_job(*values: object) -> bool:
    """Require explicit AI/data-science evidence in source-provided fields."""
    return any(_AI_TERMS.search(str(value or "")) for value in values)


def extract_job_posting_jsonld(html: str) -> dict | None:
    """Safely extract the first valid JobPosting JSON-LD object from HTML."""
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue

        if isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                items = data["@graph"]
            else:
                items = [data]
        elif isinstance(data, list):
            items = data
        else:
            continue

        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


class SitemapJobsAdapter(SourceAdapter):
    """Base class for adapters that discover jobs via XML sitemaps."""

    # Child classes MUST define these
    name: str = ""
    vertical: str = "jobs"
    sitemap_url: str = ""

    def __init__(self, http_client: AsyncHttpClient):
        super().__init__(http_client)
        if not self.sitemap_url:
            raise ValueError(f"{self.__class__.__name__} must define sitemap_url")

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        """Fetch the XML sitemap and yield all URLs found in <loc> tags."""
        fetch_result = await self.http_client.get(self.sitemap_url)

        if fetch_result.status_code >= 400:
            logger.warning(
                "sitemap_fetch_failed",
                url=self.sitemap_url,
                status_code=fetch_result.status_code,
            )
            return

        if not fetch_result.text or not fetch_result.text.strip():
            logger.warning("sitemap_empty", url=self.sitemap_url)
            return

        try:
            root = ET.fromstring(fetch_result.text)
        except ET.ParseError as exc:
            logger.warning("sitemap_parse_failed", error=str(exc), url=self.sitemap_url)
            return

        # Sitemaps use namespaces, typically xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        # Since namespaces can be annoying with ET, a robust trick is to strip them or find iteratively.
        # We can find all elements with local name "loc"
        found = False
        seen_urls = set()
        for elem in root.iter():
            # Extract local tag name (ignoring namespace e.g. '{http://www.sitemaps.org/...}loc')
            tag_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag_name == "loc" and elem.text:
                url = elem.text.strip()
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    found = True
                    yield DiscoveredUrl(url=url)
                    
        if not found:
            logger.warning("sitemap_no_urls_found", url=self.sitemap_url)
