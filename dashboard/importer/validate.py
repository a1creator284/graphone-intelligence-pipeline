"""Whole-workbook validation and in-memory dataset construction.

This module performs **every** check before the loader is allowed to touch the
database. It returns a fully-materialised :class:`WorkbookDataset`; if it
raises, the caller has performed zero database writes by construction, because
no session is involved here at all.

What is checked
---------------

* exactly the six expected sheet names, in order;
* required headers present on every sheet;
* expected row counts (configurable, defaulting to the verified counts);
* every ID field is a valid UUID, and record IDs are unique within a sheet;
* every timestamp is ISO-8601 *and* timezone-aware;
* every JSON cell parses into its documented container type;
* News continuation columns rejoin losslessly and the rejoined ``full_text``
  agrees with the ``full_text`` embedded in the rejoined metadata;
* every domain ``canonical_entity_id`` resolves to a mapping-log entity;
* the mapping log has exactly one explicit ``created`` decision per canonical
  entity, with a non-empty canonical/normalized name, and a consistent
  ``canonical_name`` across all its rows;
* ``raw_document_id`` equals ``provenance_id`` on every domain row, and
  repeated provenance groups agree on every exported provenance field;
* derived ``entity_type`` values are supported by the existing ORM column;
* values fit the ORM column widths and satisfy its unique constraints.

Canonical entity reconstruction
-------------------------------

The workbook does not export a ``canonical_entities`` sheet. Each canonical
entity is rebuilt from its single explicit mapping-log ``created`` row, which
carries the authoritative ``canonical_entity_id``, ``canonical_name`` and
``normalized_name``. ``created_at`` is the **earliest** mapping-log
``created_at`` for that canonical id -- never import time.

entity_type policy (application classification, NOT source metadata)
--------------------------------------------------------------------

``entity_type`` is *not* exported by the workbook. It is assigned by this
deterministic, evidence-only policy:

1. referenced by >= 1 Startup row  -> ``company``
2. referenced by >= 1 Job row      -> ``company``
3. otherwise                       -> ``other``

Names are never inspected, and being a Hugging Face / OpenRouter publisher is
never treated as evidence of being a company. If the ORM's documented
vocabulary ever stops supporting the value this policy produces, validation
raises :class:`EntityTypeUnsupportedError` instead of inventing a value.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from dashboard.importer.workbook import (
    SHEET_ORDER,
    Cell,
    WorkbookFormatError,
    as_bool,
    as_datetime,
    as_float,
    as_int,
    as_json,
    as_text,
    as_uuid,
    blank,
    read_workbook,
)

# Verified counts for submission/graphone_final.xlsx. Passing
# ``expected_counts=None`` skips the count assertion (used by unit fixtures);
# the real import always asserts them.
EXPECTED_SHEET_COUNTS: dict[str, int] = {
    "Startups": 1000,
    "Products": 1000,
    "Research Papers": 1000,
    "Jobs": 0,
    "News": 45,
    "Entity Mapping Log": 2000,
}

# The vocabulary documented on CanonicalEntity.entity_type in
# src/storage/models.py (``# company|other``). Confirmed to include "other",
# so the policy below never needs to invent a value.
SUPPORTED_ENTITY_TYPES: frozenset[str] = frozenset({"company", "other"})

ENTITY_TYPE_COMPANY = "company"
ENTITY_TYPE_OTHER = "other"

# Mapping methods the ORM documents for EntityMappingLog.method, plus the
# "created" decision the resolver writes when it mints a new canonical entity.
KNOWN_MAPPING_METHODS: frozenset[str] = frozenset(
    {"created", "normalized_exact", "alias", "fuzzy", "unresolved"}
)

PROVENANCE_FIELDS: tuple[str, ...] = (
    "provenance_id",
    "provenance_source_name",
    "provenance_source_url",
    "provenance_canonical_url",
    "provenance_retrieved_at",
    "provenance_http_status",
    "provenance_content_hash",
    "provenance_raw_content_location",
    "provenance_extraction_status",
    "provenance_publication_date_candidates",
    "provenance_crawl_run_id",
)

REQUIRED_HEADERS: dict[str, tuple[str, ...]] = {
    "Startups": (
        "id", "entity_name", "canonical_entity_id", "source_name", "source_url",
        "employee_count", "raw_document_id", "collected_at", "export_as_of_utc",
    ) + PROVENANCE_FIELDS,
    "Products": (
        "id", "product_name", "startup_name", "canonical_entity_id", "source_name",
        "source_url", "source_external_id", "dedup_key", "pricing_model",
        "metadata_json", "raw_document_id", "collected_at", "export_as_of_utc",
    ) + PROVENANCE_FIELDS,
    "Research Papers": (
        "id", "title", "authors", "paper_url", "paper_external_id", "github_url",
        "github_stars", "published_date", "source_name", "raw_document_id",
        "collected_at", "export_as_of_utc",
    ) + PROVENANCE_FIELDS,
    "Jobs": (
        "id", "company", "canonical_entity_id", "title", "url", "posted_at",
        "is_remote", "role_family", "source_name", "raw_document_id",
        "metadata_json", "collected_at", "export_as_of_utc",
    ) + PROVENANCE_FIELDS,
    "News": (
        "id", "title", "url", "source_name", "published_at", "full_text_location",
        "extracted_metadata", "raw_document_id", "collected_at",
        "export_as_of_utc", "full_text", "full_text_status",
    ) + PROVENANCE_FIELDS,
    "Entity Mapping Log": (
        "id", "raw_name", "normalized_name", "canonical_name", "canonical_entity_id",
        "method", "confidence", "source_url", "created_at", "export_as_of_utc",
    ),
}

PRICING_MODELS: frozenset[str] = frozenset({"FREE", "FREEMIUM", "PAID", "ENTERPRISE"})


class WorkbookValidationError(ValueError):
    """Validation failed. No database write has been attempted."""


class EntityTypeUnsupportedError(WorkbookValidationError):
    """A derived entity_type cannot be represented under the existing schema.

    Raised instead of guessing or inventing a value, per the import policy.
    """


# ---------------------------------------------------------------------------
# Materialised, validated dataset
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidatedDataset:
    """Everything the loader needs, already coerced and cross-checked."""

    source_path: str
    raw_documents: list[dict[str, Any]]
    canonical_entities: list[dict[str, Any]]
    mapping_log: list[dict[str, Any]]
    startups: list[dict[str, Any]]
    products: list[dict[str, Any]]
    research_papers: list[dict[str, Any]]
    jobs: list[dict[str, Any]]
    news: list[dict[str, Any]]
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def entity_type_distribution(self) -> dict[str, int]:
        return dict(Counter(e["entity_type"] for e in self.canonical_entities))

    def counts(self) -> dict[str, int]:
        return {
            "raw_documents": len(self.raw_documents),
            "canonical_entities": len(self.canonical_entities),
            "entity_aliases": 0,  # see the documented alias limitation
            "entity_mapping_log": len(self.mapping_log),
            "startups": len(self.startups),
            "products": len(self.products),
            "research_papers": len(self.research_papers),
            "jobs": len(self.jobs),
            "news": len(self.news),
        }


# Back-compat / friendlier alias used by the package's public surface.
WorkbookDataset = ValidatedDataset


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _cell(sheet: str, row: dict[str, Any], index: int, column: str) -> Cell:
    return Cell(sheet=sheet, row=index, column=column, value=row.get(column))


def _check_headers(sheet: str, header: list[str]) -> None:
    missing = [h for h in REQUIRED_HEADERS[sheet] if h not in header]
    if missing:
        raise WorkbookValidationError(f"sheet '{sheet}' is missing required headers: {missing}")


def _check_counts(sheets: dict[str, tuple[list[str], list[dict]]], expected: dict[str, int] | None) -> None:
    if expected is None:
        return
    mismatches = {
        name: {"expected": count, "found": len(sheets[name][1])}
        for name, count in expected.items()
        if len(sheets[name][1]) != count
    }
    if mismatches:
        raise WorkbookValidationError(f"unexpected row counts: {mismatches}")


def _unique(sheet: str, values: list[Any], label: str) -> None:
    duplicates = [value for value, count in Counter(values).items() if count > 1 and value is not None]
    if duplicates:
        raise WorkbookValidationError(
            f"sheet '{sheet}' has duplicate {label}: {sorted(map(str, duplicates))[:5]}"
        )


def _provenance_signature(sheet: str, row: dict[str, Any], index: int) -> dict[str, Any]:
    """Coerce the eleven exported provenance columns into a RawDocument row."""
    document_id = as_uuid(_cell(sheet, row, index, "provenance_id"), required=True)
    record_raw_id = as_uuid(_cell(sheet, row, index, "raw_document_id"))
    if record_raw_id != document_id:
        raise WorkbookValidationError(
            f"{sheet} row {index}: raw_document_id {record_raw_id} does not match provenance_id {document_id}"
        )
    return {
        "id": document_id,
        "source_name": as_text(_cell(sheet, row, index, "provenance_source_name"), required=True, max_length=120),
        "source_url": as_text(_cell(sheet, row, index, "provenance_source_url"), required=True),
        "canonical_url": as_text(_cell(sheet, row, index, "provenance_canonical_url"), required=True),
        "retrieved_at": as_datetime(_cell(sheet, row, index, "provenance_retrieved_at"), required=True),
        "http_status": as_int(_cell(sheet, row, index, "provenance_http_status")),
        "content_hash": as_text(_cell(sheet, row, index, "provenance_content_hash"), required=True, max_length=64),
        "raw_content_location": as_text(_cell(sheet, row, index, "provenance_raw_content_location")),
        "extraction_status": as_text(
            _cell(sheet, row, index, "provenance_extraction_status"), required=True, max_length=20
        ),
        "publication_date_candidates": as_json(
            _cell(sheet, row, index, "provenance_publication_date_candidates"), expected=dict, default={}
        ),
        # crawl_run_id is a FK to crawl_runs. The workbook exports no crawl-run
        # rows, so a populated value here cannot be satisfied without inventing
        # a parent crawl run -- which the policy forbids. Validation rejects it.
        "crawl_run_id": as_uuid(_cell(sheet, row, index, "provenance_crawl_run_id")),
    }


def _collect_raw_documents(
    per_sheet_rows: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    documents: dict[UUID, dict[str, Any]] = {}
    origins: dict[UUID, str] = {}
    for sheet, rows in per_sheet_rows.items():
        for index, row in enumerate(rows, start=2):
            if blank(row.get("provenance_id")) and blank(row.get("raw_document_id")):
                continue  # a record exported without provenance; nothing to rebuild
            document = _provenance_signature(sheet, row, index)
            document_id = document["id"]
            previous = documents.get(document_id)
            if previous is None:
                documents[document_id] = document
                origins[document_id] = f"{sheet} row {index}"
                continue
            if previous != document:
                differing = sorted(k for k in document if previous[k] != document[k])
                raise WorkbookValidationError(
                    f"raw document {document_id} is exported inconsistently "
                    f"({origins[document_id]} vs {sheet} row {index}); differing fields: {differing}"
                )

    for document in documents.values():
        if document["crawl_run_id"] is not None:
            raise WorkbookValidationError(
                f"raw document {document['id']} references crawl run {document['crawl_run_id']}, but the "
                "workbook exports no crawl runs. Refusing to invent a crawl_runs parent row."
            )

    hashes = Counter(d["content_hash"] for d in documents.values())
    collisions = [h for h, count in hashes.items() if count > 1]
    if collisions:
        raise WorkbookValidationError(
            "raw documents violate uq_raw_documents_content_hash; duplicate content hashes: "
            f"{collisions[:5]}"
        )
    return [documents[key] for key in sorted(documents, key=str)]


def _validate_mapping_log(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sheet = "Entity Mapping Log"
    records: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        method = as_text(_cell(sheet, row, index, "method"), required=True, max_length=30)
        if method not in KNOWN_MAPPING_METHODS:
            raise WorkbookValidationError(
                f"{sheet} row {index}: unknown mapping method {method!r}; "
                f"known methods are {sorted(KNOWN_MAPPING_METHODS)}"
            )
        records.append(
            {
                "id": as_uuid(_cell(sheet, row, index, "id"), required=True),
                "raw_name": as_text(_cell(sheet, row, index, "raw_name"), required=True, max_length=250),
                "normalized_name": as_text(
                    _cell(sheet, row, index, "normalized_name"), required=True, max_length=250
                ),
                "canonical_name": as_text(_cell(sheet, row, index, "canonical_name"), max_length=250),
                "canonical_entity_id": as_uuid(_cell(sheet, row, index, "canonical_entity_id")),
                "method": method,
                "confidence": as_float(_cell(sheet, row, index, "confidence"), required=True),
                "source_url": as_text(_cell(sheet, row, index, "source_url")),
                "created_at": as_datetime(_cell(sheet, row, index, "created_at"), required=True),
            }
        )
    _unique(sheet, [r["id"] for r in records], "record ids")
    return records


def _reconstruct_canonical_entities(
    mapping_log: list[dict[str, Any]],
    *,
    company_ids: set[UUID],
) -> list[dict[str, Any]]:
    """Rebuild canonical_entities from explicit ``created`` mapping decisions."""
    by_entity: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    for record in mapping_log:
        if record["canonical_entity_id"] is not None:
            by_entity[record["canonical_entity_id"]].append(record)

    entities: list[dict[str, Any]] = []
    for entity_id, records in by_entity.items():
        creations = [r for r in records if r["method"] == "created"]
        if len(creations) != 1:
            raise WorkbookValidationError(
                f"canonical entity {entity_id} has {len(creations)} explicit 'created' mapping decisions; "
                "exactly one is required to reconstruct it without fabricating names"
            )
        creation = creations[0]
        if not creation["canonical_name"] or not creation["normalized_name"]:
            raise WorkbookValidationError(
                f"canonical entity {entity_id}: its 'created' mapping record "
                f"({creation['id']}) has an empty canonical/normalized name"
            )
        names = {r["canonical_name"] for r in records if r["canonical_name"]}
        if names != {creation["canonical_name"]}:
            raise WorkbookValidationError(
                f"canonical entity {entity_id} has inconsistent canonical names across its "
                f"mapping records: {sorted(names)}"
            )
        entities.append(
            {
                "id": entity_id,
                "canonical_name": creation["canonical_name"],
                "normalized_name": creation["normalized_name"],
                # Application classification -- see the module docstring.
                "entity_type": ENTITY_TYPE_COMPANY if entity_id in company_ids else ENTITY_TYPE_OTHER,
                # Earliest explicit mapping timestamp, never import time.
                "created_at": min(r["created_at"] for r in records),
            }
        )

    unsupported = sorted({e["entity_type"] for e in entities} - SUPPORTED_ENTITY_TYPES)
    if unsupported:
        raise EntityTypeUnsupportedError(
            f"derived entity_type value(s) {unsupported} are not supported by "
            f"CanonicalEntity.entity_type (documented vocabulary: {sorted(SUPPORTED_ENTITY_TYPES)}). "
            "Refusing to invent a value."
        )
    over_wide = [e["id"] for e in entities if len(e["entity_type"]) > 30]
    if over_wide:
        raise EntityTypeUnsupportedError(
            f"derived entity_type exceeds the 30-character column width for entities {over_wide[:5]}"
        )

    _unique(
        "Entity Mapping Log",
        [e["normalized_name"] for e in entities],
        "reconstructed normalized_name values (violates uq_canonical_entities_normalized_name)",
    )
    return sorted(entities, key=lambda e: str(e["id"]))


def _validate_startups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sheet = "Startups"
    records = []
    for index, row in enumerate(rows, start=2):
        records.append(
            {
                "id": as_uuid(_cell(sheet, row, index, "id"), required=True),
                "entity_name": as_text(_cell(sheet, row, index, "entity_name"), required=True, max_length=250),
                "canonical_entity_id": as_uuid(_cell(sheet, row, index, "canonical_entity_id")),
                "source_name": as_text(_cell(sheet, row, index, "source_name"), required=True, max_length=120),
                "source_url": as_text(_cell(sheet, row, index, "source_url"), required=True),
                "employee_count": as_int(_cell(sheet, row, index, "employee_count"), minimum=0),
                "raw_document_id": as_uuid(_cell(sheet, row, index, "raw_document_id")),
                "collected_at": as_datetime(_cell(sheet, row, index, "collected_at"), required=True),
            }
        )
    _unique(sheet, [r["id"] for r in records], "record ids")
    _unique(
        sheet,
        [(r["source_name"], r["source_url"]) for r in records],
        "(source_name, source_url) pairs (violates uq_startups_source_url)",
    )
    return records


def _validate_products(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sheet = "Products"
    records = []
    for index, row in enumerate(rows, start=2):
        pricing = as_text(_cell(sheet, row, index, "pricing_model"), max_length=30)
        if pricing is not None and pricing not in PRICING_MODELS:
            raise WorkbookValidationError(
                f"{sheet} row {index}: pricing_model {pricing!r} is outside the "
                f"pricing_model_enum vocabulary {sorted(PRICING_MODELS)}"
            )
        records.append(
            {
                "id": as_uuid(_cell(sheet, row, index, "id"), required=True),
                "product_name": as_text(_cell(sheet, row, index, "product_name"), max_length=250),
                "startup_name": as_text(_cell(sheet, row, index, "startup_name"), required=True, max_length=250),
                "canonical_entity_id": as_uuid(_cell(sheet, row, index, "canonical_entity_id")),
                "source_name": as_text(_cell(sheet, row, index, "source_name"), required=True, max_length=120),
                "source_url": as_text(_cell(sheet, row, index, "source_url"), required=True),
                "source_external_id": as_text(_cell(sheet, row, index, "source_external_id"), max_length=250),
                "dedup_key": as_text(_cell(sheet, row, index, "dedup_key"), max_length=300),
                "pricing_model": pricing,
                "metadata_json": as_json(
                    _cell(sheet, row, index, "metadata_json"), expected=dict, default={}
                ),
                "raw_document_id": as_uuid(_cell(sheet, row, index, "raw_document_id")),
                "collected_at": as_datetime(_cell(sheet, row, index, "collected_at"), required=True),
            }
        )
    _unique(sheet, [r["id"] for r in records], "record ids")
    _unique(
        sheet,
        [(r["source_name"], r["source_url"]) for r in records],
        "(source_name, source_url) pairs (violates uq_products_source_url)",
    )
    _unique(
        sheet,
        [r["dedup_key"] for r in records if r["dedup_key"] is not None],
        "dedup_key values (violates uq_products_dedup_key)",
    )
    return records


def _validate_research_papers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sheet = "Research Papers"
    records = []
    for index, row in enumerate(rows, start=2):
        records.append(
            {
                "id": as_uuid(_cell(sheet, row, index, "id"), required=True),
                "title": as_text(_cell(sheet, row, index, "title"), required=True),
                "authors": as_json(_cell(sheet, row, index, "authors"), expected=list, default=[]),
                "paper_url": as_text(_cell(sheet, row, index, "paper_url"), required=True),
                "paper_external_id": as_text(_cell(sheet, row, index, "paper_external_id"), max_length=120),
                "github_url": as_text(_cell(sheet, row, index, "github_url")),
                "github_stars": as_int(_cell(sheet, row, index, "github_stars"), minimum=0),
                "published_date": as_datetime(_cell(sheet, row, index, "published_date")),
                "source_name": as_text(_cell(sheet, row, index, "source_name"), required=True, max_length=120),
                "raw_document_id": as_uuid(_cell(sheet, row, index, "raw_document_id")),
                "collected_at": as_datetime(_cell(sheet, row, index, "collected_at"), required=True),
            }
        )
    _unique(sheet, [r["id"] for r in records], "record ids")
    _unique(sheet, [r["paper_url"] for r in records], "paper_url values (violates uq_research_papers_paper_url)")
    return records


def _validate_jobs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sheet = "Jobs"
    records = []
    for index, row in enumerate(rows, start=2):
        records.append(
            {
                "id": as_uuid(_cell(sheet, row, index, "id"), required=True),
                "company": as_text(_cell(sheet, row, index, "company"), required=True, max_length=250),
                "canonical_entity_id": as_uuid(_cell(sheet, row, index, "canonical_entity_id")),
                "title": as_text(_cell(sheet, row, index, "title"), required=True),
                "url": as_text(_cell(sheet, row, index, "url"), required=True),
                "posted_at": as_datetime(_cell(sheet, row, index, "posted_at"), required=True),
                "is_remote": as_bool(_cell(sheet, row, index, "is_remote")),
                "role_family": as_text(_cell(sheet, row, index, "role_family"), max_length=60),
                "source_name": as_text(_cell(sheet, row, index, "source_name"), required=True, max_length=120),
                "raw_document_id": as_uuid(_cell(sheet, row, index, "raw_document_id")),
                "metadata_json": as_json(_cell(sheet, row, index, "metadata_json"), expected=dict, default={}),
                "collected_at": as_datetime(_cell(sheet, row, index, "collected_at"), required=True),
            }
        )
    _unique(sheet, [r["id"] for r in records], "record ids")
    _unique(
        sheet,
        [(r["source_name"], r["url"]) for r in records],
        "(source_name, url) pairs (violates uq_jobs_source_url)",
    )
    return records


def _validate_news(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate News, including the continuation-column reconstruction check.

    ``full_text`` is an *export-only* column: the ORM stores the article body
    inside ``extracted_metadata`` (with ``full_text_location`` recording where
    it came from). So the rejoined ``full_text`` must equal the ``full_text``
    inside the rejoined ``extracted_metadata``; otherwise the continuation
    columns were rejoined wrongly and the record would be imported corrupted.
    """
    sheet = "News"
    records = []
    for index, row in enumerate(rows, start=2):
        metadata = as_json(_cell(sheet, row, index, "extracted_metadata"), expected=dict, default={})
        full_text = as_text(_cell(sheet, row, index, "full_text"))
        status = as_text(_cell(sheet, row, index, "full_text_status"))
        inline = metadata.get("full_text")
        if status == "inline" or isinstance(inline, str):
            if full_text != inline:
                raise WorkbookValidationError(
                    f"{sheet} row {index}: reassembled full_text ({len(full_text or '')} chars) does not match "
                    f"the full_text inside reassembled extracted_metadata ({len(inline or '') if isinstance(inline, str) else 'absent'} chars); "
                    "continuation columns are inconsistent"
                )
        records.append(
            {
                "id": as_uuid(_cell(sheet, row, index, "id"), required=True),
                "title": as_text(_cell(sheet, row, index, "title"), required=True),
                "url": as_text(_cell(sheet, row, index, "url"), required=True),
                "source_name": as_text(_cell(sheet, row, index, "source_name"), required=True, max_length=120),
                "published_at": as_datetime(_cell(sheet, row, index, "published_at"), required=True),
                "full_text_location": as_text(_cell(sheet, row, index, "full_text_location")),
                "extracted_metadata": metadata,
                "raw_document_id": as_uuid(_cell(sheet, row, index, "raw_document_id")),
                "collected_at": as_datetime(_cell(sheet, row, index, "collected_at"), required=True),
            }
        )
    _unique(sheet, [r["id"] for r in records], "record ids")
    _unique(
        sheet,
        [(r["source_name"], r["url"]) for r in records],
        "(source_name, url) pairs (violates uq_news_source_url)",
    )
    return records


def _check_foreign_keys(
    dataset_rows: dict[str, list[dict[str, Any]]],
    entity_ids: set[UUID],
    document_ids: set[UUID],
) -> None:
    for label, rows in dataset_rows.items():
        for row in rows:
            entity_id = row.get("canonical_entity_id")
            if entity_id is not None and entity_id not in entity_ids:
                raise WorkbookValidationError(
                    f"{label} record {row['id']} references canonical entity {entity_id}, which has no "
                    "explicit mapping-log creation record"
                )
            document_id = row.get("raw_document_id")
            if document_id is not None and document_id not in document_ids:
                raise WorkbookValidationError(
                    f"{label} record {row['id']} references raw document {document_id}, which was not "
                    "reconstructable from the exported provenance columns"
                )


def validate_workbook(
    path: str | Path,
    *,
    expected_counts: dict[str, int] | None = EXPECTED_SHEET_COUNTS,
) -> ValidatedDataset:
    """Validate the whole workbook and return the dataset ready to load.

    Raises :class:`WorkbookValidationError` (or
    :class:`~dashboard.importer.workbook.WorkbookFormatError`) on any problem.
    Performs no database access whatsoever, which is what guarantees "zero
    writes on validation failure".
    """
    path = Path(path)
    if not path.is_file():
        raise WorkbookValidationError(f"workbook not found: {path}")

    try:
        sheets = read_workbook(path)
    except WorkbookFormatError as exc:
        raise WorkbookValidationError(str(exc)) from exc

    for name in SHEET_ORDER:
        _check_headers(name, sheets[name][0])
    _check_counts(sheets, expected_counts)

    rows = {name: sheets[name][1] for name in SHEET_ORDER}

    try:
        mapping_log = _validate_mapping_log(rows["Entity Mapping Log"])
        startups = _validate_startups(rows["Startups"])
        products = _validate_products(rows["Products"])
        research_papers = _validate_research_papers(rows["Research Papers"])
        jobs = _validate_jobs(rows["Jobs"])
        news = _validate_news(rows["News"])
        raw_documents = _collect_raw_documents(
            {
                "Startups": rows["Startups"],
                "Products": rows["Products"],
                "Research Papers": rows["Research Papers"],
                "Jobs": rows["Jobs"],
                "News": rows["News"],
            }
        )
    except WorkbookFormatError as exc:
        raise WorkbookValidationError(str(exc)) from exc

    # entity_type evidence: Startup and Job references only (documented policy).
    company_ids = {r["canonical_entity_id"] for r in startups if r["canonical_entity_id"]}
    company_ids |= {r["canonical_entity_id"] for r in jobs if r["canonical_entity_id"]}

    canonical_entities = _reconstruct_canonical_entities(mapping_log, company_ids=company_ids)
    entity_ids = {e["id"] for e in canonical_entities}
    document_ids = {d["id"] for d in raw_documents}

    _check_foreign_keys(
        {
            "Startups": startups,
            "Products": products,
            "Research Papers": research_papers,
            "Jobs": jobs,
            "News": news,
        },
        entity_ids,
        document_ids,
    )
    for record in mapping_log:
        if record["canonical_entity_id"] is not None and record["canonical_entity_id"] not in entity_ids:
            raise WorkbookValidationError(
                f"mapping record {record['id']} references unknown canonical entity "
                f"{record['canonical_entity_id']}"
            )

    dataset = ValidatedDataset(
        source_path=str(path),
        raw_documents=raw_documents,
        canonical_entities=canonical_entities,
        mapping_log=mapping_log,
        startups=startups,
        products=products,
        research_papers=research_papers,
        jobs=jobs,
        news=news,
    )
    report = {
        "source_path": str(path),
        "counts": dataset.counts(),
        "mapping_methods": dict(Counter(r["method"] for r in mapping_log)),
        "entity_types": dataset.entity_type_distribution,
        "entity_type_policy": "startup-or-job reference => company; otherwise other (application classification)",
        "company_evidence_entities": len(company_ids),
        "aliases_imported": 0,
        "alias_limitation": (
            "The workbook exports no entity_aliases rows or alias ids, so none are created. "
            "The 7 'alias' and 3 'fuzzy' mapping decisions are preserved in entity_mapping_log as audit records."
        ),
        "earliest_mapping_created_at": (
            min(r["created_at"] for r in mapping_log).isoformat() if mapping_log else None
        ),
    }
    object.__setattr__(dataset, "report", report)
    return dataset


def summarise(dataset: ValidatedDataset) -> str:
    lines = [f"workbook: {dataset.source_path}", "validated counts:"]
    for key, value in dataset.counts().items():
        lines.append(f"  {key:22s} {value}")
    lines.append(f"entity_type distribution: {dataset.entity_type_distribution}")
    lines.append(f"mapping methods: {dataset.report.get('mapping_methods')}")
    return "\n".join(lines)


def isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
