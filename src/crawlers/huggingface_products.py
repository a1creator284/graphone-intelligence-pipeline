"""
Hugging Face Hub adapters for the PRODUCTS vertical.

Two concrete adapters share one base class because the Hub exposes both
listings through the *same* documented public REST surface:

* ``https://huggingface.co/api/spaces``  -> deployed AI applications
  ("Spaces"): real, running, publicly-listed AI products.
* ``https://huggingface.co/api/models``  -> published AI models: the model
  artifacts that vendors ship as products.

Both are official, unauthenticated, documented endpoints
(https://huggingface.co/docs/hub/api). No login, no HTML scraping, no
robots.txt bypass.

Pagination (and why the YC bug cannot recur here)
-------------------------------------------------
The Hub paginates with an **opaque cursor advertised in the ``Link`` response
header** (``<...&cursor=...>; rel="next"``). There is no client-side offset
arithmetic at all, so the class of bug fixed in 6c3f225 -- shrinking
``hitsPerPage`` on the final page and sliding an offset window *backwards*
onto already-seen records -- is structurally impossible: the server hands us
the exact continuation token for the records it has not yet returned.

The legacy ``skip=`` offset parameter is explicitly deprecated by the Hub
("Use the `links` response header instead"), so it is deliberately not used.

Because the next page's URL is only knowable once the current page has been
fetched, ``discover()`` performs the HTTP GET itself and caches the real
``FetchResult``; ``fetch()`` then returns that cached result instead of
re-requesting the same URL. Provenance is unaffected -- the cached object is
the genuine response (status, body, sha256 content hash).

Field discipline (anti-fabrication)
-----------------------------------
Every persisted value is a verbatim substring/field of the API response:

* ``product_name``       <- ``cardData.title`` when the author published one,
  otherwise the repo segment of the Hub ``id``. Never invented.
* ``startup_name``       <- ``author`` (the publishing org/user), falling back
  to the owner segment of ``id``. A record with neither is **skipped**.
* ``source_url``         <- the canonical Hub URL built from ``id``.
* ``source_external_id`` <- the Hub ``id`` itself (the record's own key).
* ``pricing_model``      <- always NULL. The Hub publishes no price for
  Spaces or models, so nothing is derived from tagline/description keywords.
* ``metadata_json``      <- only verbatim numeric/string fields the API
  returned (likes, downloads, sdk, pipeline_tag, timestamps, tags).

Nothing is estimated, rounded, keyword-guessed, or back-filled.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from src.config.logging import get_logger
from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.crawlers.http import FetchResult
from src.errors import ParsingError, PipelineError

logger = get_logger(component="huggingface_products_adapter")

HF_API_BASE = "https://huggingface.co/api"
HF_SITE_BASE = "https://huggingface.co"

# The Hub caps `limit` at 1000; 100 is the polite default and keeps each
# response small enough to hash/store cheaply.
MAX_PAGE_SIZE = 1000

_NEXT_LINK_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"', re.IGNORECASE)


def parse_next_link(headers: dict[str, str]) -> str | None:
    """Extract the ``rel="next"`` URL from a Hub ``Link`` header.

    Returns None when the header is absent or advertises no next page -- that
    is the source telling us the listing is exhausted, and it is honoured
    rather than worked around.
    """
    for key, value in headers.items():
        if key.lower() != "link" or not value:
            continue
        match = _NEXT_LINK_RE.search(value)
        if match:
            return match.group(1)
    return None


class _HuggingFaceListingAdapter(SourceAdapter):
    """Shared cursor-paged listing logic for the Hub's REST listings."""

    vertical = "products"

    #: API path segment ("spaces" / "models")
    listing: str
    #: prefix for the deterministic cross-source dedup key
    dedup_prefix: str

    def __init__(
        self,
        http_client,
        *,
        max_results: int = 100,
        page_size: int = 100,
        sort: str = "likes",
        extra_params: dict[str, str] | None = None,
    ):
        super().__init__(http_client)
        self.max_results = max(0, max_results)
        self.page_size = max(1, min(page_size, MAX_PAGE_SIZE))
        self.sort = sort
        self.extra_params = dict(extra_params or {})
        # url -> already-fetched FetchResult (see module docstring).
        self._page_cache: dict[str, FetchResult] = {}

    # ---- URL construction -------------------------------------------------

    def first_page_url(self) -> str:
        from urllib.parse import urlencode

        params = {
            "limit": str(self.page_size),
            "sort": self.sort,
            "direction": "-1",
            "full": "true",
            **self.extra_params,
        }
        return f"{HF_API_BASE}/{self.listing}?{urlencode(params)}"

    # ---- discovery --------------------------------------------------------

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        if self.max_results == 0:
            return

        url: str | None = self.first_page_url()
        emitted = 0
        page_index = 0

        while url is not None and emitted < self.max_results:
            try:
                result = await self.http_client.get(url)
            except PipelineError as exc:
                # A dead/blocked page ends discovery honestly; it never
                # produces a synthesized page of records.
                logger.warning(
                    "hf_discovery_fetch_failed",
                    source=self.name,
                    url=url,
                    page=page_index,
                    error=str(exc),
                )
                return

            try:
                items = json.loads(result.text)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "hf_discovery_malformed_page",
                    source=self.name,
                    url=url,
                    page=page_index,
                    error=str(exc),
                )
                # Still hand the page downstream so parse() records the
                # failure through the normal error path rather than silently
                # swallowing a broken source.
                self._page_cache[url] = result
                yield DiscoveredUrl(
                    url=url,
                    metadata={"page": page_index, "max_records": 0, "source": self.name},
                )
                return

            if not isinstance(items, list) or not items:
                # Empty list == the listing is exhausted. Stop; do not pad.
                return

            # The window we still want. `page_size` is NEVER shrunk to make
            # this fit -- the server owns the cursor, and the ceiling is
            # applied by trimming inside parse() via `max_records`.
            take = min(len(items), self.max_results - emitted)

            self._page_cache[url] = result
            yield DiscoveredUrl(
                url=url,
                metadata={"page": page_index, "max_records": take, "source": self.name},
            )

            emitted += take
            page_index += 1
            url = parse_next_link(result.headers)

    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        cached = self._page_cache.pop(discovered.url, None)
        if cached is not None:
            return cached
        return await self.http_client.get(discovered.url)

    # ---- parsing ----------------------------------------------------------

    async def parse(self, fetch_result: FetchResult, discovered: DiscoveredUrl) -> list[ParsedRecord]:
        try:
            payload = json.loads(fetch_result.text)
        except json.JSONDecodeError as exc:
            raise ParsingError(
                f"Malformed JSON from Hugging Face {self.listing} listing: {exc}",
                context={"url": fetch_result.url},
            ) from exc

        if isinstance(payload, dict) and "error" in payload:
            # An API error body is not "zero products" -- surface it.
            raise ParsingError(
                f"Hugging Face {self.listing} listing returned an error: {payload.get('error')}",
                context={"url": fetch_result.url},
            )

        if not isinstance(payload, list):
            raise ParsingError(
                f"Hugging Face {self.listing} listing payload is not a JSON array",
                context={"url": fetch_result.url},
            )

        max_records = discovered.metadata.get("max_records")
        limit = max_records if isinstance(max_records, int) and max_records >= 0 else None

        records: list[ParsedRecord] = []
        seen: set[str] = set()
        for item in payload:
            if limit is not None and len(records) >= limit:
                break
            if not isinstance(item, dict):
                continue
            record = self._parse_item(item, fetch_result)
            if record is None:
                continue
            if record.source_url in seen:
                continue
            seen.add(record.source_url)
            records.append(record)
        return records

    # ---- per-record mapping (subclass hooks) ------------------------------

    def _site_url(self, repo_id: str) -> str:
        raise NotImplementedError

    def _extra_metadata(self, item: dict) -> dict:
        return {}

    def _product_name(self, item: dict, repo_id: str) -> str | None:
        card = item.get("cardData")
        if isinstance(card, dict):
            title = card.get("title")
            if isinstance(title, str) and title.strip():
                return " ".join(title.split())
        repo = repo_id.split("/")[-1].strip()
        return repo or None

    def _parse_item(self, item: dict, fetch_result: FetchResult) -> ParsedRecord | None:
        repo_id = item.get("id") or item.get("modelId")
        if not isinstance(repo_id, str):
            return None
        repo_id = repo_id.strip().strip("/")
        if not repo_id:
            return None

        product_name = self._product_name(item, repo_id)
        vendor = self._vendor(item, repo_id)
        if not product_name or not vendor:
            # A required identity field is genuinely absent -- skip rather
            # than synthesize an owner or a title.
            return None

        metadata = {
            "hub_id": repo_id,
            "tags": item.get("tags") if isinstance(item.get("tags"), list) else None,
            "created_at": item.get("createdAt") if isinstance(item.get("createdAt"), str) else None,
            "last_modified": item.get("lastModified") if isinstance(item.get("lastModified"), str) else None,
            "likes": item.get("likes") if isinstance(item.get("likes"), int) else None,
            **self._extra_metadata(item),
        }
        metadata = {k: v for k, v in metadata.items() if v is not None}

        return ParsedRecord(
            record_type="product",
            data={
                "product_name": product_name,
                "startup_name": vendor,
                "source_external_id": repo_id,
                # The Hub publishes no price for these listings.
                "pricing_model": None,
                "metadata_json": metadata,
                "dedup_key": f"{self.dedup_prefix}:{repo_id.casefold()}",
            },
            source_name=self.name,
            source_url=self._site_url(repo_id),
            fetch_result=fetch_result,
        )

    @staticmethod
    def _vendor(item: dict, repo_id: str) -> str | None:
        """The publishing org/user, verbatim.

        `author` is the Hub's own owner field. When it is absent we fall back
        to the owner segment of the `id` (``owner/repo``), which is the same
        string. A canonical repo with no owner at all yields None and the
        record is skipped -- an owner is never invented.
        """
        author = item.get("author")
        if isinstance(author, str) and author.strip():
            return author.strip()
        if "/" in repo_id:
            owner = repo_id.split("/", 1)[0].strip()
            if owner:
                return owner
        return None


class HuggingFaceSpacesAdapter(_HuggingFaceListingAdapter):
    """Publicly-listed Hugging Face Spaces (deployed AI applications)."""

    name = "huggingface_spaces"
    listing = "spaces"
    dedup_prefix = "hf-space"

    def _site_url(self, repo_id: str) -> str:
        return f"{HF_SITE_BASE}/spaces/{repo_id}"

    def _extra_metadata(self, item: dict) -> dict:
        card = item.get("cardData") if isinstance(item.get("cardData"), dict) else {}
        return {
            "sdk": item.get("sdk") if isinstance(item.get("sdk"), str) else None,
            "short_description": (
                card.get("short_description")
                if isinstance(card.get("short_description"), str)
                else None
            ),
            "license": card.get("license") if isinstance(card.get("license"), str) else None,
        }


class HuggingFaceModelsAdapter(_HuggingFaceListingAdapter):
    """Publicly-listed Hugging Face models (published AI model products)."""

    name = "huggingface_models"
    listing = "models"
    dedup_prefix = "hf-model"

    def __init__(self, http_client, **kwargs):
        # Sorting by downloads surfaces the models that are actually used as
        # products; it is the Hub's own metric, not a computed score.
        kwargs.setdefault("sort", "downloads")
        super().__init__(http_client, **kwargs)

    def _site_url(self, repo_id: str) -> str:
        return f"{HF_SITE_BASE}/{repo_id}"

    def _product_name(self, item: dict, repo_id: str) -> str | None:
        # Model repos rarely carry cardData.title; the repo segment is the
        # model's published name.
        repo = repo_id.split("/")[-1].strip()
        return repo or None

    def _extra_metadata(self, item: dict) -> dict:
        return {
            "downloads": item.get("downloads") if isinstance(item.get("downloads"), int) else None,
            "pipeline_tag": item.get("pipeline_tag") if isinstance(item.get("pipeline_tag"), str) else None,
            "library_name": item.get("library_name") if isinstance(item.get("library_name"), str) else None,
        }
