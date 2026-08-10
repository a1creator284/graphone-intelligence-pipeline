"""
Papers With Code adapter (Section 10).

Uses the official Papers With Code REST API (paperswithcode.com/api/v1) --
JSON responses, no scraping. The API exposes a `repository` relation for
papers that have one; we only ever use that link, never inferring a repo
from a similar name (Section 10).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.crawlers.http import FetchResult
from src.errors import ParsingError
from src.extraction.dates import parse_absolute_date
from src.extraction.github import parse_github_repo_url


class PapersWithCodeAdapter(SourceAdapter):
    name = "papers_with_code"
    vertical = "research"

    API_BASE = "https://paperswithcode.com/api/v1"

    def __init__(self, http_client, *, max_results: int = 100, page_size: int = 50):
        super().__init__(http_client)
        self.max_results = max_results
        self.page_size = page_size

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        """The PWC API paginates via `page`; each discovered item is one
        page of the /papers/ listing (parse() expands it into records)."""
        page_size = min(self.page_size, 50)  # API caps items_per_page at 50
        num_pages = max(1, -(-self.max_results // page_size))  # ceil div
        for page in range(1, num_pages + 1):
            url = f"{self.API_BASE}/papers/?page={page}&items_per_page={page_size}"
            yield DiscoveredUrl(url=url, metadata={"page": page})

    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        return await self.http_client.get(discovered.url)

    async def parse(self, fetch_result: FetchResult, discovered: DiscoveredUrl) -> list[ParsedRecord]:
        try:
            payload = json.loads(fetch_result.text)
        except json.JSONDecodeError as exc:
            raise ParsingError(f"Malformed JSON from Papers With Code: {exc}", context={"url": fetch_result.url}) from exc

        results = payload.get("results", [])
        records: list[ParsedRecord] = []

        for paper in results:
            title = (paper.get("title") or "").strip()
            paper_url = paper.get("url_abs") or paper.get("id_url")
            paper_id = paper.get("id")

            if not title or not paper_url:
                # Missing required fields -- skip rather than guess (Section 48).
                continue

            authors = paper.get("authors") or []
            published_date = parse_absolute_date(paper["published"]) if paper.get("published") else None

            # PWC's own repository relation, when present, is a github URL.
            # Validate it through the same parser the enrichment client uses
            # -- if it's not a real github.com/owner/repo URL, treat it as
            # absent rather than passing a bad value downstream.
            repo_field = paper.get("repository")
            github_url = None
            if isinstance(repo_field, dict):
                candidate = repo_field.get("url")
                if candidate and parse_github_repo_url(candidate):
                    github_url = candidate
            elif isinstance(repo_field, str) and parse_github_repo_url(repo_field):
                github_url = repo_field

            records.append(
                ParsedRecord(
                    record_type="research_paper",
                    data={
                        "title": title,
                        "authors": authors,
                        "paper_url": paper_url,
                        "paper_external_id": paper_id,
                        "github_url": github_url,
                        "github_stars": None,  # populated by GitHubEnrichmentClient only
                        "published_date": published_date,
                    },
                    source_name=self.name,
                    source_url=paper_url,
                    fetch_result=fetch_result,
                )
            )
        return records
