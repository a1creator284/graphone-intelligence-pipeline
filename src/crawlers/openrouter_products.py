"""
OpenRouter model-catalog adapter (products vertical).

Source
------
``https://openrouter.ai/api/v1/models`` is OpenRouter's official, public,
unauthenticated model catalogue (documented at
https://openrouter.ai/docs/api-reference/list-available-models). Every entry
is a commercially-listed AI product: a model an end user can actually buy
inference on, with the vendor's published per-token price.

This is the only source wired here that publishes a **real price**, which is
why it is the only source allowed to populate ``pricing_model``. The mapping
is a direct read of the published numbers, not a keyword guess:

* both ``pricing.prompt`` and ``pricing.completion`` are exactly ``0``
  -> ``FREE``
* any positive published price                                -> ``PAID``
* pricing absent / unparseable                                -> ``NULL``

``FREEMIUM`` and ``ENTERPRISE`` are deliberately never emitted: the catalogue
publishes no field that supports either claim, and inferring them from a
model's name or description would be fabrication.

Pagination
----------
The endpoint takes explicit ``offset``/``limit`` query parameters and reports
``total_count``. The offset advances by a **constant** ``limit`` (the page
size is never shrunk on the final page); the run-level ceiling is applied by
trimming inside ``parse()``. This is precisely the discipline that the YC
pagination fix (6c3f225) established -- a shrinking page size would slide the
offset window backwards and re-serve records already seen.

Field discipline (anti-fabrication)
-----------------------------------
* ``product_name``       <- ``name`` verbatim (e.g. "Inception: Mercury 2.5")
* ``startup_name``       <- the vendor. Taken from the ``"Vendor: Model"``
  prefix of ``name`` when present, otherwise the owner segment of the
  slug ``id`` (``vendor/model``). Both are strings the API itself returned;
  neither is looked up, expanded, or prettified.
* ``source_url``         <- ``https://openrouter.ai/<id>``, the catalogue's
  own canonical model page.
* ``source_external_id`` <- ``id`` (the record's own key)
* ``metadata_json``      <- only verbatim API fields (canonical_slug,
  hugging_face_id, context_length, modality, published prices, created).

Descriptions, founders, funding, and logos are not fetched or invented.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from urllib.parse import urlencode

from src.config.logging import get_logger
from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.crawlers.http import FetchResult
from src.errors import ParsingError

logger = get_logger(component="openrouter_products_adapter")

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_SITE_BASE = "https://openrouter.ai"

DEFAULT_PAGE_SIZE = 100
# Hard stop on page count so a source-side pagination change can never turn
# into an unbounded request loop.
MAX_PAGES = 100


def classify_pricing(pricing: object) -> str | None:
    """Map OpenRouter's published per-token prices onto the pricing enum.

    Returns ``None`` (not a guess) whenever the catalogue does not publish a
    parseable price for both prompt and completion.
    """
    if not isinstance(pricing, dict):
        return None

    values = []
    for key in ("prompt", "completion"):
        raw = pricing.get(key)
        if isinstance(raw, bool) or raw is None:
            return None
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            return None

    if any(v < 0 for v in values):
        return None
    if all(v == 0.0 for v in values):
        return "FREE"
    return "PAID"


class OpenRouterModelsAdapter(SourceAdapter):
    name = "openrouter_models"
    vertical = "products"

    def __init__(
        self,
        http_client,
        *,
        max_results: int = 100,
        page_size: int = DEFAULT_PAGE_SIZE,
    ):
        super().__init__(http_client)
        self.max_results = max(0, max_results)
        # Constant across every page of the run -- see module docstring.
        self.page_size = max(1, page_size)

    # ---- URL construction -------------------------------------------------

    def _page_url(self, offset: int) -> str:
        return f"{OPENROUTER_API_URL}?{urlencode({'offset': offset, 'limit': self.page_size})}"

    # ---- discovery --------------------------------------------------------

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        wanted = self.max_results
        if wanted == 0:
            return

        emitted = 0
        page = 0
        while emitted < wanted and page < MAX_PAGES:
            offset = page * self.page_size
            take = min(self.page_size, wanted - emitted)
            yield DiscoveredUrl(
                url=self._page_url(offset),
                metadata={"page": page, "offset": offset, "max_records": take, "source": self.name},
            )
            emitted += take
            page += 1

    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        return await self.http_client.get(discovered.url)

    # ---- parsing ----------------------------------------------------------

    async def parse(self, fetch_result: FetchResult, discovered: DiscoveredUrl) -> list[ParsedRecord]:
        try:
            payload = json.loads(fetch_result.text)
        except json.JSONDecodeError as exc:
            raise ParsingError(
                f"Malformed JSON from OpenRouter model catalogue: {exc}",
                context={"url": fetch_result.url},
            ) from exc

        if not isinstance(payload, dict):
            raise ParsingError(
                "OpenRouter payload is not a JSON object", context={"url": fetch_result.url}
            )
        if "data" not in payload:
            # An error body has no `data`; returning [] would make a broken
            # source look like an empty one.
            raise ParsingError(
                f"OpenRouter response has no 'data' key (error body?): "
                f"{payload.get('error') or payload.get('message') or 'unknown'}",
                context={"url": fetch_result.url},
            )

        items = payload.get("data")
        if not isinstance(items, list):
            raise ParsingError(
                "OpenRouter 'data' is not a list", context={"url": fetch_result.url}
            )

        max_records = discovered.metadata.get("max_records")
        limit = max_records if isinstance(max_records, int) and max_records >= 0 else None

        records: list[ParsedRecord] = []
        seen: set[str] = set()
        for item in items:
            if limit is not None and len(records) >= limit:
                break
            if not isinstance(item, dict):
                continue
            record = self._parse_model(item, fetch_result)
            if record is None:
                continue
            if record.source_url in seen:
                continue
            seen.add(record.source_url)
            records.append(record)
        return records

    def _parse_model(self, item: dict, fetch_result: FetchResult) -> ParsedRecord | None:
        model_id = item.get("id")
        if not isinstance(model_id, str):
            return None
        model_id = model_id.strip().strip("/")
        if not model_id:
            return None

        raw_name = item.get("name")
        product_name = " ".join(str(raw_name).split()) if isinstance(raw_name, str) else ""
        if not product_name:
            return None

        vendor = self._vendor(product_name, model_id)
        if not vendor:
            return None

        metadata = {
            "openrouter_id": model_id,
            "canonical_slug": item.get("canonical_slug") if isinstance(item.get("canonical_slug"), str) else None,
            "hugging_face_id": item.get("hugging_face_id") if isinstance(item.get("hugging_face_id"), str) else None,
            "context_length": item.get("context_length") if isinstance(item.get("context_length"), int) else None,
            "modality": self._modality(item),
            "pricing_prompt": self._price_str(item, "prompt"),
            "pricing_completion": self._price_str(item, "completion"),
            "created": item.get("created") if isinstance(item.get("created"), int) else None,
        }
        metadata = {k: v for k, v in metadata.items() if v is not None}

        return ParsedRecord(
            record_type="product",
            data={
                "product_name": product_name,
                "startup_name": vendor,
                "source_external_id": model_id,
                "pricing_model": classify_pricing(item.get("pricing")),
                "metadata_json": metadata,
                # Cross-source identity key. When OpenRouter names the exact
                # Hugging Face repo behind a model, that repo id is the
                # shared key so the same artifact listed on both sources
                # collapses to one product. Otherwise the key is
                # source-scoped and the record stays distinct.
                "dedup_key": self._dedup_key(item, model_id),
            },
            source_name=self.name,
            source_url=f"{OPENROUTER_SITE_BASE}/{model_id}",
            fetch_result=fetch_result,
        )

    @staticmethod
    def _dedup_key(item: dict, model_id: str) -> str:
        hf_id = item.get("hugging_face_id")
        if isinstance(hf_id, str) and hf_id.strip():
            return f"hf-model:{hf_id.strip().strip('/').casefold()}"
        return f"openrouter:{model_id.casefold()}"

    @staticmethod
    def _price_str(item: dict, key: str) -> str | None:
        pricing = item.get("pricing")
        if not isinstance(pricing, dict):
            return None
        value = pricing.get(key)
        # Stored verbatim as the source published it (a decimal string),
        # so no float rounding is introduced into the provenance record.
        return str(value) if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None

    @staticmethod
    def _modality(item: dict) -> str | None:
        arch = item.get("architecture")
        if isinstance(arch, dict):
            modality = arch.get("modality")
            if isinstance(modality, str) and modality.strip():
                return modality.strip()
        return None

    @staticmethod
    def _vendor(product_name: str, model_id: str) -> str | None:
        """Publishing vendor, taken verbatim from data the API returned.

        OpenRouter formats display names as ``"Vendor: Model"`` and slugs as
        ``vendor/model``. Both halves are the source's own strings; the
        vendor is never looked up elsewhere or expanded into a legal name.
        """
        if ":" in product_name:
            candidate = product_name.split(":", 1)[0].strip()
            if candidate:
                return candidate
        if "/" in model_id:
            candidate = model_id.split("/", 1)[0].strip()
            if candidate:
                return candidate
        return None
