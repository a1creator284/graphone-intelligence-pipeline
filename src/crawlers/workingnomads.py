from __future__ import annotations

import json
from collections.abc import AsyncIterator

from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.crawlers.http import FetchResult
from src.errors import ParsingError
from src.crawlers.sitemap_jobs_base import is_ai_job
from src.validation.job_dates import parse_job_timestamp
from src.extraction.urls import normalize_url


class WorkingNomadsAIAdapter(SourceAdapter):
    name = "workingnomads_ai_jobs"
    vertical = "jobs"

    def __init__(self, http_client, category: str | None = None):
        super().__init__(http_client)
        self.category = category or "data"

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        # Filter by category=data as per config
        url = f"https://www.workingnomads.com/api/exposed_jobs?category={self.category}"
        yield DiscoveredUrl(url=url)

    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        return await self.http_client.get(discovered.url)

    async def parse(self, fetch_result: FetchResult, discovered: DiscoveredUrl) -> list[ParsedRecord]:
        try:
            payload = json.loads(fetch_result.text)
        except json.JSONDecodeError as exc:
            raise ParsingError(f"Malformed JSON from WorkingNomads: {exc}", context={"url": fetch_result.url}) from exc

        if not isinstance(payload, list):
            raise ParsingError("Expected a JSON list from WorkingNomads", context={"url": fetch_result.url})

        records: list[ParsedRecord] = []

        for item in payload:
            if not isinstance(item, dict):
                continue

            title = (item.get("title") or "").strip()
            company = (item.get("company_name") or "").strip()
            url = (item.get("url") or "").strip()

            if not title or not company or not url:
                continue

            if not is_ai_job(title, item.get("tags"), item.get("description")):
                continue

            posted_at = parse_job_timestamp(item["pub_date"]) if item.get("pub_date") else None

            records.append(
                ParsedRecord(
                    record_type="job",
                    data={
                        "title": title,
                        "company": company,
                        "url": normalize_url(url),
                        "source_name": self.name,
                        "posted_at": posted_at,
                        "is_remote": True,
                        "role_family": None,
                        "raw_document_id": None,
                        "metadata_json": {"raw_record": item, "date_field": "pub_date",
                                          "date_value": item.get("pub_date"), "source_url": url},
                    },
                    source_name=self.name,
                    source_url=normalize_url(url),
                    fetch_result=fetch_result,
                )
            )
        return records
