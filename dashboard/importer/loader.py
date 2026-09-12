"""Transactional loader for the validated workbook dataset.

Guarantees
----------

**Validate first.** :func:`import_dataset` only ever receives an already
validated :class:`~dashboard.importer.validate.ValidatedDataset`. The CLI
validates before opening a session, so a validation failure performs zero
database writes.

**One transaction.** Everything -- the optional replace/reset, the canonical
entities, the raw documents, the mapping log and all five domain tables --
happens inside a single ``session.begin()`` block. Any exception (including a
database integrity error) rolls the whole thing back, so the database is never
left holding a half-imported dataset.

**Idempotent.** Every table is keyed by the workbook's own primary key. Rows
that already exist are updated in place with the same source values; rows that
do not exist are inserted. Re-running the import therefore leaves row counts,
ids and relationships identical and creates no duplicate raw documents.

**Bounded blast radius.** The optional replace step deletes rows only from the
nine tables this importer owns, in FK-safe order. It never issues ``TRUNCATE``,
never touches ``crawl_runs``, ``crawl_jobs``, ``sources``, ``llm_requests`` or
``processing_errors``, and never drops or creates tables.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.importer.validate import ValidatedDataset
from src.storage.models import (
    CanonicalEntity,
    EntityAlias,
    EntityMappingLog,
    Job,
    News,
    Product,
    RawDocument,
    ResearchPaper,
    Startup,
)

# Insert order: parents before children. Reverse of this is the delete order.
LOAD_ORDER: tuple[tuple[str, Any], ...] = (
    ("canonical_entities", CanonicalEntity),
    ("raw_documents", RawDocument),
    ("entity_mapping_log", EntityMappingLog),
    ("startups", Startup),
    ("products", Product),
    ("research_papers", ResearchPaper),
    ("jobs", Job),
    ("news", News),
)

# Tables the replace/reset step is allowed to clear, child-first so foreign
# keys stay satisfied at every step. entity_aliases is included because it is a
# child of canonical_entities: leaving orphan demo aliases behind would break
# the FK when the demo entities are removed.
RESET_ORDER: tuple[tuple[str, Any], ...] = (
    ("news", News),
    ("jobs", Job),
    ("research_papers", ResearchPaper),
    ("products", Product),
    ("startups", Startup),
    ("entity_mapping_log", EntityMappingLog),
    ("entity_aliases", EntityAlias),
    ("raw_documents", RawDocument),
    ("canonical_entities", CanonicalEntity),
)

# Tables the importer must never modify, documented so the behaviour is
# reviewable rather than implied.
UNTOUCHED_TABLES: tuple[str, ...] = (
    "sources",
    "crawl_runs",
    "crawl_jobs",
    "llm_requests",
    "processing_errors",
)


@dataclass
class ImportResult:
    """What the import actually did, per table."""

    mode: str
    inserted: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    deleted: dict[str, int] = field(default_factory=dict)
    final_counts: dict[str, int] = field(default_factory=dict)
    aliases_created: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "inserted": self.inserted,
            "updated": self.updated,
            "deleted": self.deleted,
            "final_counts": self.final_counts,
            "aliases_created": self.aliases_created,
            "untouched_tables": list(UNTOUCHED_TABLES),
            "notes": self.notes,
        }


async def table_counts(session: AsyncSession) -> dict[str, int]:
    """Row counts for the nine tables this importer owns."""
    counts: dict[str, int] = {}
    for label, model in (*LOAD_ORDER, ("entity_aliases", EntityAlias)):
        counts[label] = await session.scalar(select(func.count()).select_from(model)) or 0
    return counts


async def _upsert(
    session: AsyncSession,
    model: Any,
    records: Sequence[dict[str, Any]],
) -> tuple[int, int]:
    """Insert-or-update by primary key. Returns ``(inserted, updated)``.

    The workbook carries authoritative primary keys, so identity is never
    guessed. Existing rows are refreshed field-by-field from the workbook,
    which is what makes a second run a no-op rather than a duplicate.
    """
    if not records:
        return 0, 0

    ids = [record["id"] for record in records]
    existing: dict[Any, Any] = {}
    # Chunked IN(...) lookups keep the statement well under driver parameter
    # limits for the 1000-row sheets.
    for chunk in _chunks(ids, 500):
        result = await session.execute(select(model).where(model.id.in_(chunk)))
        for row in result.scalars():
            existing[row.id] = row

    inserted = updated = 0
    for record in records:
        current = existing.get(record["id"])
        if current is None:
            session.add(model(**record))
            inserted += 1
            continue
        for key, value in record.items():
            if key == "id":
                continue
            if getattr(current, key) != value:
                setattr(current, key, value)
        updated += 1
    await session.flush()
    return inserted, updated


def _chunks(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


async def _reset_owned_tables(session: AsyncSession) -> dict[str, int]:
    """Delete rows from the nine importer-owned tables, child-first.

    Uses ``DELETE`` (not ``TRUNCATE``) so the operation participates in the
    surrounding transaction and can be rolled back, and so no unrelated table
    is ever implicated by a cascade.
    """
    deleted: dict[str, int] = {}
    for label, model in RESET_ORDER:
        result = await session.execute(delete(model))
        deleted[label] = result.rowcount or 0
    await session.flush()
    return deleted


async def import_dataset(
    session: AsyncSession,
    dataset: ValidatedDataset,
    *,
    replace: bool = False,
) -> ImportResult:
    """Load a validated dataset in one transaction.

    Parameters
    ----------
    replace:
        When true, first delete all rows from the nine importer-owned tables
        (see :data:`RESET_ORDER`) so the final dataset does not get mixed with
        a pre-existing demo dataset. When false, the import upserts onto
        whatever is already there, which is the idempotent re-run path.

    The caller must not have an open transaction; this function owns the
    transaction boundary so that a failure rolls back *everything*, including
    the replace step.
    """
    result = ImportResult(mode="replace" if replace else "upsert")

    async with session.begin():
        if replace:
            result.deleted = await _reset_owned_tables(session)

        payload: dict[str, Sequence[dict[str, Any]]] = {
            "canonical_entities": dataset.canonical_entities,
            "raw_documents": dataset.raw_documents,
            "entity_mapping_log": dataset.mapping_log,
            "startups": dataset.startups,
            "products": dataset.products,
            "research_papers": dataset.research_papers,
            "jobs": dataset.jobs,
            "news": dataset.news,
        }
        for label, model in LOAD_ORDER:
            inserted, updated = await _upsert(session, model, payload[label])
            result.inserted[label] = inserted
            result.updated[label] = updated

        result.final_counts = await table_counts(session)

    # entity_aliases is deliberately left alone: the workbook exports no alias
    # rows or alias ids, so there is nothing authoritative to import and the
    # importer refuses to synthesise any.
    result.aliases_created = 0
    result.notes.append(
        "entity_aliases: no rows created -- the workbook exports no alias records or alias ids. "
        "The 'alias'/'fuzzy' resolution decisions remain preserved in entity_mapping_log."
    )
    result.notes.append(
        "crawl_runs: no rows created -- the workbook exports no crawl runs, and "
        "raw_documents.crawl_run_id is left NULL rather than pointing at an invented parent."
    )
    result.notes.append(
        "raw_documents.raw_content_location is imported exactly as exported (NULL throughout this "
        "workbook); no raw payload bytes are invented or fetched."
    )
    if dataset.jobs == []:
        result.notes.append("jobs: 0 rows, matching the workbook exactly. No jobs were fabricated.")
    return result
