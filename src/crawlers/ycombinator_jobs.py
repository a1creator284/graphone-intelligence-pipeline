from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from bs4 import BeautifulSoup

from src.config.logging import get_logger
from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.crawlers.http import FetchResult
from src.errors import ParsingError
from src.crawlers.sitemap_jobs_base import is_ai_job
from src.validation.job_dates import parse_job_timestamp
from src.extraction.urls import normalize_url

logger = get_logger(component="ycombinator_adapter")


class YCombinatorWhoIsHiringAdapter(SourceAdapter):
    name = "ycombinator_hn_whoishiring"
    vertical = "jobs"

    def __init__(self, http_client):
        super().__init__(http_client)

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        import urllib.parse
        
        # 1. Discover the most recent "Who is hiring?" thread
        query = '"Ask HN: Who is hiring?"'
        params = urllib.parse.urlencode({
            "query": query,
            "tags": "story,author_whoishiring",
            "hitsPerPage": 1
        })
        search_url = f"https://hn.algolia.com/api/v1/search_by_date?{params}"
        
        search_fetch = await self.http_client.get(search_url)
        try:
            search_data = json.loads(search_fetch.text)
        except json.JSONDecodeError as exc:
            raise ParsingError(f"Malformed HN story search: {exc}") from exc
        
        if not isinstance(search_data, dict) or not isinstance(search_data.get("hits"), list):
            raise ParsingError("HN story search is missing its hits list")
        hits = search_data["hits"]
        if not hits:
            logger.info("ycombinator_no_whoishiring_thread_found")
            return
            
        story = hits[0]
        story_id = story.get("objectID")
        if not story_id:
            return
            
        # 2. Yield the items API URL for this story to fetch all comments
        item_url = f"https://hn.algolia.com/api/v1/items/{story_id}"
        yield DiscoveredUrl(
            url=item_url,
            metadata={"story_created_at": story.get("created_at")}
        )

    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        return await self.http_client.get(discovered.url)

    async def parse(self, fetch_result: FetchResult, discovered: DiscoveredUrl) -> list[ParsedRecord]:
        try:
            payload = json.loads(fetch_result.text)
        except json.JSONDecodeError as exc:
            raise ParsingError(f"Malformed JSON from HN Items API: {exc}", context={"url": fetch_result.url}) from exc

        if not isinstance(payload, dict) or "children" not in payload or not isinstance(payload["children"], list):
            raise ParsingError("Expected a 'children' list in HN Items response", context={"url": fetch_result.url})
        
        children = payload["children"]
        records: list[ParsedRecord] = []

        for child in children:
            if not isinstance(child, dict):
                continue
                
            text_html = child.get("text")
            if not isinstance(text_html, str) or not text_html or child.get("dead") or child.get("deleted"):
                continue
                
            comment_id = child.get("id")
            if not str(comment_id or "").isdigit():
                continue
            soup = BeautifulSoup(text_html, "lxml")
            if not is_ai_job(soup.get_text(" ", strip=True)):
                continue
            a_tag = soup.find("a", href=True)
            application_url = a_tag["href"] if a_tag else None
            first_line = BeautifulSoup(re.split(r"<p[^>]*>|<br\s*/?>", text_html, maxsplit=1, flags=re.I)[0], "lxml")
            # Links are not employer names; keep only the literal name text.
            for anchor in first_line.find_all("a"):
                anchor.decompose()
            parts = [p.strip() for p in first_line.get_text(" ", strip=True).split("|")]
            if len(parts) < 2:
                continue
            company = parts[0]
            # Locate an explicit role segment rather than calling "Remote" a title.
            title = next((part for part in parts[1:] if re.search(
                r"\b(?:engineers?|developers?|scientists?|researchers?|analysts?|designers?|architects?|"
                r"managers?|MLOps|DevOps|CTO|roles?|positions?)\b", part, re.I
            )), None)
            if not title or not company:
                continue
            url = f"https://news.ycombinator.com/item?id={comment_id}"
            posted_at = parse_job_timestamp(child.get("created_at"))

            records.append(
                ParsedRecord(
                    record_type="job",
                    data={
                        "title": title,
                        "company": company,
                        "url": normalize_url(url),
                        "source_name": self.name,
                        "posted_at": posted_at,
                        "is_remote": None,
                        "role_family": None,
                        "raw_document_id": None,
                        "metadata_json": {"raw_record": child, "date_field": "created_at",
                                          "date_value": child.get("created_at"), "source_url": url,
                                          "application_url": application_url,
                                          "story_created_at": discovered.metadata.get("story_created_at")},
                    },
                    source_name=self.name,
                    source_url=normalize_url(url),
                    fetch_result=fetch_result,
                )
            )
            
        return records
