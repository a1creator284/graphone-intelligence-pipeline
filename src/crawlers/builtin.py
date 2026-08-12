from __future__ import annotations

from src.config.logging import get_logger
from src.crawlers.base import DiscoveredUrl, ParsedRecord
from src.crawlers.http import FetchResult
from src.crawlers.sitemap_jobs_base import SitemapJobsAdapter, extract_job_posting_jsonld
from src.extraction.dates import parse_absolute_date
from src.extraction.urls import normalize_url

logger = get_logger(component="builtin_adapter")


class BuiltInAIAdapter(SitemapJobsAdapter):
    name = "builtin_ai_jobs"
    sitemap_url = "https://builtin.com/jobs/ai-machine-learning/sitemap.xml"

    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        return await self.http_client.get(discovered.url)

    async def parse(self, fetch_result: FetchResult, discovered: DiscoveredUrl) -> list[ParsedRecord]:
        if not fetch_result.text:
            return []

        job_posting = extract_job_posting_jsonld(fetch_result.text)
        if not job_posting:
            return []

        title = job_posting.get("title")
        org = job_posting.get("hiringOrganization", {})
        company = org.get("name") if isinstance(org, dict) else None

        url_raw = job_posting.get("url") or discovered.url
        posted_at_raw = job_posting.get("datePosted")

        if not title or not company or not url_raw or not posted_at_raw:
            return []

        posted_at = parse_absolute_date(posted_at_raw)
        if not posted_at:
            return []

        url = normalize_url(url_raw)

        return [
            ParsedRecord(
                record_type="job",
                data={
                    "title": title,
                    "company": company,
                    "url": url,
                    "source_name": self.name,
                    "posted_at": posted_at,
                    "is_remote": None,
                    "role_family": None,
                    "raw_document_id": None,
                },
                source_name=self.name,
                source_url=url,
                fetch_result=fetch_result,
            )
        ]
