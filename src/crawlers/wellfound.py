from __future__ import annotations

from src.config.logging import get_logger
from src.crawlers.base import DiscoveredUrl, ParsedRecord
from src.crawlers.http import FetchResult
from src.crawlers.sitemap_jobs_base import SitemapJobsAdapter, extract_job_posting_jsonld, is_ai_job
from src.validation.job_dates import parse_job_timestamp
from src.extraction.urls import normalize_url

logger = get_logger(component="wellfound_adapter")


class WellfoundAIAdapter(SitemapJobsAdapter):
    name = "wellfound_ai_jobs"
    sitemap_url = "https://wellfound.com/sitemap.xml"

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

        if not title or not company or not url_raw:
            return []

        if not is_ai_job(title, job_posting.get("description")):
            return []

        posted_at = parse_job_timestamp(posted_at_raw)

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
                    "metadata_json": {"raw_record": job_posting, "date_field": "datePosted",
                                      "date_value": posted_at_raw, "source_url": discovered.url},
                },
                source_name=self.name,
                source_url=discovered.url,
                fetch_result=fetch_result,
            )
        ]
