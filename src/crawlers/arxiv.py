"""
arXiv adapter (Section 10).

Uses arXiv's official Atom API (export.arxiv.org/api/query) -- an official
API per the anti-bot Tier 1 preference (Section 31). No scraping of the HTML
listing pages.

GitHub repository association: arXiv does not structurally link papers to
repos. This adapter only extracts a GitHub URL when one appears explicitly
in the abstract/summary text (a common author convention: "Code available
at https://github.com/..."). It never infers a repo from a similar name
(Section 10: "Do not infer a repository solely from a similar name").
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator
from xml.etree import ElementTree as ET

from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.crawlers.http import FetchResult
from src.errors import ParsingError
from src.extraction.dates import parse_absolute_date
from src.extraction.github import parse_github_repo_url

ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"

_GITHUB_URL_IN_TEXT = re.compile(r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def extract_github_url_from_text(text: str) -> str | None:
    """Find the first github.com/owner/repo URL mentioned in free text, or
    None. Validated via the same parser the enrichment client uses, so a
    malformed match is discarded rather than passed downstream."""
    if not text:
        return None
    for candidate in _GITHUB_URL_IN_TEXT.findall(text):
        cleaned = candidate.rstrip(").,;:'\"")
        if parse_github_repo_url(cleaned):
            return cleaned
    return None


class ArxivAdapter(SourceAdapter):
    name = "arxiv"
    vertical = "research"

    API_BASE = "https://export.arxiv.org/api/query"

    def __init__(self, http_client, *, search_query: str = "cat:cs.AI", max_results: int = 100, page_size: int = 50):
        super().__init__(http_client)
        self.search_query = search_query
        self.max_results = max_results
        self.page_size = page_size

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        """arXiv's API is itself paginated via start/max_results query
        params -- each 'discovered URL' is one page of the Atom feed, which
        parse() then expands into individual paper records."""
        for start in range(0, self.max_results, self.page_size):
            count = min(self.page_size, self.max_results - start)
            url = (
                f"{self.API_BASE}?search_query={self.search_query}"
                f"&sortBy=submittedDate&sortOrder=descending"
                f"&start={start}&max_results={count}"
            )
            yield DiscoveredUrl(url=url, metadata={"start": start, "count": count})

    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        return await self.http_client.get(discovered.url)

    async def parse(self, fetch_result: FetchResult, discovered: DiscoveredUrl) -> list[ParsedRecord]:
        try:
            root = ET.fromstring(fetch_result.text)
        except ET.ParseError as exc:
            raise ParsingError(f"Malformed Atom XML from arXiv: {exc}", context={"url": fetch_result.url}) from exc

        records: list[ParsedRecord] = []
        for entry in root.findall(f"{ATOM_NS}entry"):
            title_el = entry.find(f"{ATOM_NS}title")
            id_el = entry.find(f"{ATOM_NS}id")
            published_el = entry.find(f"{ATOM_NS}published")
            summary_el = entry.find(f"{ATOM_NS}summary")
            comment_el = entry.find(f"{ARXIV_NS}comment")

            if title_el is None or id_el is None:
                # Malformed entry -- skip rather than guess required fields.
                continue

            paper_url = id_el.text.strip() if id_el.text else None
            if not paper_url:
                continue

            title = " ".join((title_el.text or "").split())
            authors = [
                " ".join((name.text or "").split())
                for author in entry.findall(f"{ATOM_NS}author")
                if (name := author.find(f"{ATOM_NS}name")) is not None and name.text
            ]
            published_date = parse_absolute_date(published_el.text) if published_el is not None else None

            summary_text = summary_el.text or "" if summary_el is not None else ""
            comment_text = comment_el.text or "" if comment_el is not None else ""
            github_url = extract_github_url_from_text(summary_text) or extract_github_url_from_text(comment_text)

            arxiv_id_match = re.search(r"abs/([\w.]+)", paper_url)
            paper_external_id = arxiv_id_match.group(1) if arxiv_id_match else None

            records.append(
                ParsedRecord(
                    record_type="research_paper",
                    data={
                        "title": title,
                        "authors": authors,
                        "paper_url": paper_url,
                        "paper_external_id": paper_external_id,
                        # github_stars is deliberately absent here -- it is
                        # populated later by GitHubEnrichmentClient, never
                        # guessed by this adapter (Section 10).
                        "github_url": github_url,
                        "github_stars": None,
                        "published_date": published_date,
                    },
                    source_name=self.name,
                    source_url=paper_url,
                    fetch_result=fetch_result,
                )
            )
        return records
