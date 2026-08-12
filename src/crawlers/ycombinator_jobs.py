from __future__ import annotations

import json
from collections.abc import AsyncIterator

from bs4 import BeautifulSoup

from src.config.logging import get_logger
from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.crawlers.http import FetchResult
from src.errors import ParsingError
from src.extraction.dates import parse_absolute_date
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
            "tags": "story",
            "hitsPerPage": 1
        })
        search_url = f"https://hn.algolia.com/api/v1/search_by_date?{params}"
        
        search_fetch = await self.http_client.get(search_url)
        try:
            search_data = json.loads(search_fetch.text)
        except json.JSONDecodeError as exc:
            logger.warning("ycombinator_search_json_failed", error=str(exc))
            return
        
        hits = search_data.get("hits", [])
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

        if "children" not in payload or not isinstance(payload["children"], list):
            raise ParsingError("Expected a 'children' list in HN Items response", context={"url": fetch_result.url})
        
        children = payload["children"]
        records: list[ParsedRecord] = []

        for child in children:
            if not isinstance(child, dict):
                continue
                
            text_html = child.get("text")
            if not text_html:
                continue
                
            # Extract URL: first link in the comment
            soup = BeautifulSoup(text_html, "lxml")
            a_tag = soup.find("a")
            url = a_tag["href"] if a_tag and a_tag.has_attr("href") else ""
            
            # Heuristic: split first paragraph by '|'
            first_p_html = text_html.split("<p>")[0]
            first_p_text = BeautifulSoup(first_p_html, "lxml").get_text(separator=" ", strip=True)
            
            parts = [p.strip() for p in first_p_text.split("|")]
            if len(parts) < 2:
                # Not formatted as "Company | Title | ...", skip it
                continue
                
            company = parts[0]
            title = parts[1]
            
            if not title or not company or not url:
                continue
                
            posted_at = parse_absolute_date(child.get("created_at", ""))
            if not posted_at:
                continue

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
                    },
                    source_name=self.name,
                    source_url=normalize_url(url),
                    fetch_result=fetch_result,
                )
            )
            
        return records
