from __future__ import annotations

import json
from collections.abc import AsyncIterator

from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.crawlers.http import FetchResult
from src.errors import ParsingError
from src.crawlers.sitemap_jobs_base import is_ai_job
from src.validation.job_dates import parse_job_timestamp
from src.extraction.urls import normalize_url


class RemoteOKAIAdapter(SourceAdapter):
    name = "remoteok_ai_jobs"
    vertical = "jobs"

    def __init__(self, http_client, tags: list[str] | None = None):
        super().__init__(http_client)
        self.tags = tags or ["ai", "ml"]

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        # Filtered by ai/ml tags as per source config
        tags_query = ",".join(self.tags)
        url = f"https://remoteok.com/api?tags={tags_query}"
        yield DiscoveredUrl(url=url)

    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        return await self.http_client.get(discovered.url)

    async def parse(self, fetch_result: FetchResult, discovered: DiscoveredUrl) -> list[ParsedRecord]:
        try:
            payload = json.loads(fetch_result.text)
        except json.JSONDecodeError as exc:
            raise ParsingError(f"Malformed JSON from RemoteOK: {exc}", context={"url": fetch_result.url}) from exc

        if not isinstance(payload, list):
            raise ParsingError("Expected a JSON list from RemoteOK", context={"url": fetch_result.url})

        records: list[ParsedRecord] = []

        for item in payload:
            if not isinstance(item, dict):
                continue
            
            # Skip the legal/meta block remoteok sometimes includes at index 0
            if "legal" in item:
                continue

            title = (item.get("position") or "").strip()
            company = (item.get("company") or "").strip()
            url = (item.get("url") or "").strip()

            if not title or not company or not url:
                continue

            if not is_ai_job(title, item.get("tags"), item.get("description")):
                continue

            posted_at = parse_job_timestamp(item["date"]) if item.get("date") else None
            
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
                        "metadata_json": {"raw_record": item, "date_field": "date",
                                          "date_value": item.get("date"), "source_url": url},
                    },
                    source_name=self.name,
                    source_url=normalize_url(url),
                    fetch_result=fetch_result,
                )
            )
        return records
