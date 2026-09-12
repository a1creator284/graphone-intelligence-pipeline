"""Tests for the dashboard workbook importer.

Everything here runs against a **synthetic six-tab workbook** built in a temp
directory and an **in-memory SQLite database** (the same convention as
``tests/conftest.py``). No test needs the developer's live localhost
PostgreSQL, and no test reads or writes ``submission/graphone_final.xlsx``.

The synthetic fixture deliberately mirrors the real workbook's shape:
* the exact six sheet names and the exporter's header sets;
* a canonical entity referenced by a Startup (-> ``company``);
* a canonical entity referenced only by a Product (-> ``other``), standing in
  for the IndexTeam case;
* a ``created`` mapping row plus a later ``normalized_exact`` row, so the
  earliest-timestamp rule for ``created_at`` is observable;
* ``alias``/``fuzzy`` decisions, so alias non-fabrication is observable;
* zero Jobs;
* a News row whose long text is split across ``__part_N`` continuation columns.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from openpyxl import Workbook, load_workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from dashboard.importer.loader import (
    RESET_ORDER,
    UNTOUCHED_TABLES,
    import_dataset,
    table_counts,
)
from dashboard.importer.validate import (
    EXPECTED_SHEET_COUNTS,
    PROVENANCE_FIELDS,
    REQUIRED_HEADERS,
    SUPPORTED_ENTITY_TYPES,
    EntityTypeUnsupportedError,
    WorkbookValidationError,
    validate_workbook,
)
from dashboard.importer.workbook import SHEET_ORDER, read_sheet
from src.storage.database import build_engine
from src.storage.models import (
    Base,
    CanonicalEntity,
    CrawlRun,
    EntityAlias,
    EntityMappingLog,
    Job,
    News,
    Product,
    RawDocument,
    ResearchPaper,
    Source,
    Startup,
)

# --------------------------------------------------------------------------
# Deterministic synthetic workbook
# --------------------------------------------------------------------------

T0 = datetime(2026, 9, 11, 9, 0, 0, tzinfo=timezone.utc)
EXPORT_AS_OF = (T0 + timedelta(hours=1)).isoformat()

# Stable ids so assertions can name them.
COMPANY_ENTITY = UUID("11111111-1111-4111-8111-111111111111")
# Stands in for the real IndexTeam case: a publisher referenced only by a
# Product, never by a Startup or a Job.
PUBLISHER_ENTITY = UUID("3953682d-497b-4b4d-bba2-d8ae6dc2456a")
DOC_STARTUP = UUID("22222222-2222-4222-8222-222222222222")
DOC_PRODUCT = UUID("33333333-3333-4333-8333-333333333333")
DOC_PAPER = UUID("44444444-4444-4444-8444-444444444444")
DOC_NEWS = UUID("55555555-5555-4555-8555-555555555555")
STARTUP_ID = UUID("66666666-6666-4666-8666-666666666666")
PRODUCT_ID = UUID("77777777-7777-4777-8777-777777777777")
PAPER_ID = UUID("88888888-8888-4888-8888-888888888888")
NEWS_ID = UUID("99999999-9999-4999-8999-999999999999")

MAP_CREATED_COMPANY = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
MAP_REPEAT_COMPANY = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2")
MAP_CREATED_PUBLISHER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3")
MAP_ALIAS = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4")
MAP_FUZZY = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5")

# Long enough that the exporter would split it; we split it by hand to exercise
# the continuation-column rejoin path without writing a 32k-character literal.
NEWS_BODY = "Paragraph about model releases. " * 40
NEWS_METADATA = {
    "source_name": "techcrunch_ai_rss",
    "source_url": "https://techcrunch.com/2026/09/11/example/",
    "full_text": NEWS_BODY,
    "extraction_library": "trafilatura",
    "truncated": False,
}

SYNTHETIC_COUNTS = {
    "Startups": 1,
    "Products": 1,
    "Research Papers": 1,
    "Jobs": 0,
    "News": 1,
    "Entity Mapping Log": 5,
}


def _provenance(document_id: UUID, source: str, url: str, digest: str, status: str) -> dict:
    return {
        "provenance_id": str(document_id),
        "provenance_source_name": source,
        "provenance_source_url": url,
        "provenance_canonical_url": url,
        "provenance_retrieved_at": T0.isoformat(),
        "provenance_http_status": 200,
        "provenance_content_hash": digest,
        "provenance_raw_content_location": None,
        "provenance_extraction_status": status,
        "provenance_publication_date_candidates": "{}",
        "provenance_crawl_run_id": None,
    }


def _sheet_rows() -> dict[str, list[dict]]:
    startup = {
        "id": str(STARTUP_ID),
        "entity_name": "Acme AI",
        "canonical_entity_id": str(COMPANY_ENTITY),
        "source_name": "ycombinator_directory",
        "source_url": "https://www.ycombinator.com/companies/acme-ai",
        "employee_count": 12,
        "raw_document_id": str(DOC_STARTUP),
        "collected_at": T0.isoformat(),
        **_provenance(DOC_STARTUP, "ycombinator_directory", "https://www.ycombinator.com/companies/acme-ai", "a" * 64, "parsed"),
        "resolved_canonical_name": "Acme AI",
        "export_as_of_utc": EXPORT_AS_OF,
    }
    product = {
        "id": str(PRODUCT_ID),
        "product_name": "IndexTTS-2-Demo",
        "startup_name": "IndexTeam",
        "canonical_entity_id": str(PUBLISHER_ENTITY),
        "source_name": "huggingface_spaces",
        "source_url": "https://huggingface.co/spaces/IndexTeam/IndexTTS-2-Demo",
        "source_external_id": "IndexTeam/IndexTTS-2-Demo",
        "dedup_key": "huggingface:IndexTeam/IndexTTS-2-Demo",
        "pricing_model": None,
        "metadata_json": json.dumps({"likes": 7, "sdk": "gradio"}, sort_keys=True),
        "raw_document_id": str(DOC_PRODUCT),
        "collected_at": T0.isoformat(),
        **_provenance(DOC_PRODUCT, "huggingface_spaces", "https://huggingface.co/api/spaces", "b" * 64, "parsed"),
        "resolved_canonical_name": "IndexTeam",
        "export_as_of_utc": EXPORT_AS_OF,
    }
    paper = {
        "id": str(PAPER_ID),
        "title": "A Study of Retrieval",
        "authors": json.dumps(["ada lovelace", "alan turing"]),
        "paper_url": "https://arxiv.org/abs/2609.00001",
        "paper_external_id": "2609.00001",
        "github_url": None,
        "github_stars": None,
        "published_date": (T0 - timedelta(days=2)).isoformat(),
        "source_name": "arxiv",
        "raw_document_id": str(DOC_PAPER),
        "collected_at": T0.isoformat(),
        **_provenance(DOC_PAPER, "arxiv", "https://export.arxiv.org/api/query", "c" * 64, "extracted"),
        "export_as_of_utc": EXPORT_AS_OF,
    }
    news = {
        "id": str(NEWS_ID),
        "title": "Example AI article",
        "url": "https://techcrunch.com/2026/09/11/example/",
        "source_name": "techcrunch_ai_rss",
        "published_at": (T0 - timedelta(hours=3)).isoformat(),
        "full_text_location": "inline:extracted_metadata.full_text",
        "raw_document_id": str(DOC_NEWS),
        "collected_at": T0.isoformat(),
        **_provenance(DOC_NEWS, "techcrunch_ai_rss", "https://techcrunch.com/2026/09/11/example/", "d" * 64, "extracted"),
        "export_as_of_utc": EXPORT_AS_OF,
        "full_text_status": "inline",
    }
    # Split both long strings into base + __part_2, exactly like the exporter.
    metadata_text = json.dumps(NEWS_METADATA, ensure_ascii=False, sort_keys=True)
    half = len(metadata_text) // 2
    news["extracted_metadata"] = metadata_text[:half]
    news["extracted_metadata__part_2"] = metadata_text[half:]
    body_half = len(NEWS_BODY) // 2
    news["full_text"] = NEWS_BODY[:body_half]
    news["full_text__part_2"] = NEWS_BODY[body_half:]

    mapping = [
        {
            "id": str(MAP_CREATED_COMPANY),
            "raw_name": "Acme AI",
            "normalized_name": "acme ai",
            "canonical_name": "Acme AI",
            "canonical_entity_id": str(COMPANY_ENTITY),
            "method": "created",
            "confidence": 1.0,
            "source_url": "https://www.ycombinator.com/companies/acme-ai",
            "created_at": T0.isoformat(),
            "resolved_canonical_name": "Acme AI",
            "export_as_of_utc": EXPORT_AS_OF,
        },
        {
            # Later timestamp: must NOT win the canonical created_at.
            "id": str(MAP_REPEAT_COMPANY),
            "raw_name": "Acme AI",
            "normalized_name": "acme ai",
            "canonical_name": "Acme AI",
            "canonical_entity_id": str(COMPANY_ENTITY),
            "method": "normalized_exact",
            "confidence": 1.0,
            "source_url": "https://news.example.com/acme",
            "created_at": (T0 + timedelta(minutes=30)).isoformat(),
            "resolved_canonical_name": "Acme AI",
            "export_as_of_utc": EXPORT_AS_OF,
        },
        {
            "id": str(MAP_CREATED_PUBLISHER),
            "raw_name": "IndexTeam",
            "normalized_name": "indexteam",
            "canonical_name": "IndexTeam",
            "canonical_entity_id": str(PUBLISHER_ENTITY),
            "method": "created",
            "confidence": 1.0,
            "source_url": "https://huggingface.co/spaces/IndexTeam/IndexTTS-2-Demo",
            "created_at": (T0 + timedelta(minutes=5)).isoformat(),
            "resolved_canonical_name": "IndexTeam",
            "export_as_of_utc": EXPORT_AS_OF,
        },
        {
            "id": str(MAP_ALIAS),
            "raw_name": "Acme A.I.",
            "normalized_name": "acme a i",
            "canonical_name": "Acme AI",
            "canonical_entity_id": str(COMPANY_ENTITY),
            "method": "alias",
            "confidence": 0.97,
            "source_url": "https://news.example.com/acme-ai",
            "created_at": (T0 + timedelta(minutes=40)).isoformat(),
            "resolved_canonical_name": "Acme AI",
            "export_as_of_utc": EXPORT_AS_OF,
        },
        {
            "id": str(MAP_FUZZY),
            "raw_name": "Acme  AI Inc",
            "normalized_name": "acme ai inc",
            "canonical_name": "Acme AI",
            "canonical_entity_id": str(COMPANY_ENTITY),
            "method": "fuzzy",
            "confidence": 0.91,
            "source_url": "https://news.example.com/acme-ai-inc",
            "created_at": (T0 + timedelta(minutes=45)).isoformat(),
            "resolved_canonical_name": "Acme AI",
            "export_as_of_utc": EXPORT_AS_OF,
        },
    ]
    return {
        "Startups": [startup],
        "Products": [product],
        "Research Papers": [paper],
        "Jobs": [],
        "News": [news],
        "Entity Mapping Log": mapping,
    }


def _headers(sheet: str, rows: list[dict]) -> list[str]:
    """Required headers first, then any extra keys the rows carry."""
    ordered = list(REQUIRED_HEADERS.get(sheet, ("id",)))
    if sheet in REQUIRED_HEADERS and sheet != "Entity Mapping Log":
        ordered.append("resolved_canonical_name")
    for row in rows:
        for key in row:
            if key not in ordered:
                ordered.append(key)
    return ordered


def write_workbook(path, rows: dict[str, list[dict]] | None = None, *, sheets=SHEET_ORDER) -> str:
    rows = _sheet_rows() if rows is None else rows
    book = Workbook()
    book.remove(book.active)
    for name in sheets:
        sheet = book.create_sheet(name)
        header = _headers(name, rows.get(name, []))
        sheet.append(header)
        for row in rows.get(name, []):
            sheet.append([row.get(column) for column in header])
    book.save(path)
    return str(path)


@pytest.fixture
def workbook(tmp_path):
    return write_workbook(tmp_path / "synthetic.xlsx")


@pytest.fixture
def rows():
    return _sheet_rows()


@pytest.fixture
def dataset(workbook):
    return validate_workbook(workbook, expected_counts=SYNTHETIC_COUNTS)


@pytest_asyncio.fixture
async def engine():
    """Isolated file-free SQLite engine shared across sessions in one test."""
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def sessions(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


# --------------------------------------------------------------------------
# 1-3. Sheets, headers, counts
# --------------------------------------------------------------------------


def test_exact_six_sheets_are_required(tmp_path, rows):
    path = write_workbook(tmp_path / "extra.xlsx", rows, sheets=(*SHEET_ORDER, "Bonus"))
    with pytest.raises(WorkbookValidationError, match="unexpected sheets"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_sheet_order_is_enforced(tmp_path, rows):
    swapped = ("Products", "Startups", "Research Papers", "Jobs", "News", "Entity Mapping Log")
    path = write_workbook(tmp_path / "swapped.xlsx", rows, sheets=swapped)
    with pytest.raises(WorkbookValidationError, match="unexpected sheets"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_missing_sheet_is_rejected(tmp_path, rows):
    path = write_workbook(tmp_path / "short.xlsx", rows, sheets=SHEET_ORDER[:-1])
    with pytest.raises(WorkbookValidationError, match="unexpected sheets"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_missing_required_header_is_reported(tmp_path, rows):
    """Dropping a required column must name the column, not crash later."""
    book = Workbook()
    book.remove(book.active)
    for name in SHEET_ORDER:
        sheet = book.create_sheet(name)
        header = [h for h in _headers(name, rows[name]) if h != "canonical_entity_id"]
        sheet.append(header)
        for row in rows[name]:
            sheet.append([row.get(column) for column in header])
    path = tmp_path / "noheader.xlsx"
    book.save(path)
    with pytest.raises(WorkbookValidationError, match="missing required headers.*canonical_entity_id"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_required_headers_cover_every_orm_column():
    """Guard against the ORM growing a column the importer would silently skip."""
    for sheet, model in (
        ("Startups", Startup),
        ("Products", Product),
        ("Research Papers", ResearchPaper),
        ("Jobs", Job),
        ("News", News),
        ("Entity Mapping Log", EntityMappingLog),
    ):
        columns = {c.name for c in model.__table__.columns}
        assert columns <= set(REQUIRED_HEADERS[sheet]), sheet
    assert {f"provenance_{c.name}" for c in RawDocument.__table__.columns} == set(PROVENANCE_FIELDS)


def test_unexpected_row_count_is_rejected(workbook):
    wrong = dict(SYNTHETIC_COUNTS, Startups=2)
    with pytest.raises(WorkbookValidationError, match="unexpected row counts"):
        validate_workbook(workbook, expected_counts=wrong)


def test_real_workbook_expected_counts_are_the_verified_numbers():
    """The shipped constant must stay pinned to the verified dataset."""
    assert EXPECTED_SHEET_COUNTS == {
        "Startups": 1000,
        "Products": 1000,
        "Research Papers": 1000,
        "Jobs": 0,
        "News": 45,
        "Entity Mapping Log": 2000,
    }


# --------------------------------------------------------------------------
# 4. UUID validation
# --------------------------------------------------------------------------


def test_invalid_record_uuid_is_rejected(tmp_path, rows):
    rows["Startups"][0]["id"] = "not-a-uuid"
    path = write_workbook(tmp_path / "baduuid.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="not a valid UUID"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_duplicate_record_ids_are_rejected(tmp_path, rows):
    clone = dict(rows["Startups"][0])
    clone["source_url"] = clone["source_url"] + "-2"
    rows["Startups"].append(clone)
    path = write_workbook(tmp_path / "dupid.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="duplicate record ids"):
        validate_workbook(path, expected_counts=dict(SYNTHETIC_COUNTS, Startups=2))


def test_duplicate_unique_constraint_pair_is_rejected(tmp_path, rows):
    clone = dict(rows["Startups"][0])
    clone["id"] = str(uuid4())
    rows["Startups"].append(clone)
    path = write_workbook(tmp_path / "dupurl.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="uq_startups_source_url"):
        validate_workbook(path, expected_counts=dict(SYNTHETIC_COUNTS, Startups=2))


# --------------------------------------------------------------------------
# 5-6. Canonical reconstruction + created_at policy
# --------------------------------------------------------------------------


def test_canonical_entities_come_from_explicit_created_rows(dataset):
    by_id = {e["id"]: e for e in dataset.canonical_entities}
    assert set(by_id) == {COMPANY_ENTITY, PUBLISHER_ENTITY}
    assert by_id[COMPANY_ENTITY]["canonical_name"] == "Acme AI"
    assert by_id[COMPANY_ENTITY]["normalized_name"] == "acme ai"
    assert by_id[PUBLISHER_ENTITY]["canonical_name"] == "IndexTeam"
    assert by_id[PUBLISHER_ENTITY]["normalized_name"] == "indexteam"


def test_created_at_uses_earliest_mapping_timestamp_not_import_time(dataset):
    by_id = {e["id"]: e for e in dataset.canonical_entities}
    # The company entity has mapping rows at T0, T0+30m, T0+40m, T0+45m.
    assert by_id[COMPANY_ENTITY]["created_at"] == T0
    assert by_id[PUBLISHER_ENTITY]["created_at"] == T0 + timedelta(minutes=5)
    # Nothing anywhere near "now".
    assert by_id[COMPANY_ENTITY]["created_at"] < datetime.now(timezone.utc) - timedelta(days=1)


def test_missing_created_decision_is_rejected(tmp_path, rows):
    rows["Entity Mapping Log"][0]["method"] = "normalized_exact"
    path = write_workbook(tmp_path / "nocreate.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="explicit 'created' mapping decisions"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_inconsistent_canonical_name_is_rejected(tmp_path, rows):
    rows["Entity Mapping Log"][1]["canonical_name"] = "Acme Artificial Intelligence"
    path = write_workbook(tmp_path / "badname.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="inconsistent canonical names"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_unknown_canonical_reference_is_rejected(tmp_path, rows):
    rows["Startups"][0]["canonical_entity_id"] = str(uuid4())
    path = write_workbook(tmp_path / "badref.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="no explicit mapping-log creation record"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


# --------------------------------------------------------------------------
# 7-9. entity_type policy
# --------------------------------------------------------------------------


def test_startup_reference_yields_company(dataset):
    by_id = {e["id"]: e for e in dataset.canonical_entities}
    assert by_id[COMPANY_ENTITY]["entity_type"] == "company"


def test_indexteam_style_publisher_is_other_not_company(dataset):
    """A Hugging Face publisher with no Startup/Job evidence must be 'other'."""
    by_id = {e["id"]: e for e in dataset.canonical_entities}
    assert by_id[PUBLISHER_ENTITY]["canonical_name"] == "IndexTeam"
    assert by_id[PUBLISHER_ENTITY]["entity_type"] == "other"
    assert dataset.entity_type_distribution == {"company": 1, "other": 1}


def test_job_reference_also_yields_company(tmp_path, rows):
    """Rule 2: a Job reference is company evidence even with no Startup row."""
    rows["Jobs"].append(
        {
            "id": str(uuid4()),
            "company": "IndexTeam",
            "canonical_entity_id": str(PUBLISHER_ENTITY),
            "title": "ML Engineer",
            "url": "https://example.com/jobs/1",
            "posted_at": T0.isoformat(),
            "is_remote": True,
            "role_family": "ml",
            "source_name": "remoteok",
            "raw_document_id": str(DOC_PRODUCT),
            "metadata_json": "{}",
            "collected_at": T0.isoformat(),
            **_provenance(DOC_PRODUCT, "huggingface_spaces", "https://huggingface.co/api/spaces", "b" * 64, "parsed"),
            "resolved_canonical_name": "IndexTeam",
            "export_as_of_utc": EXPORT_AS_OF,
        }
    )
    path = write_workbook(tmp_path / "withjob.xlsx", rows)
    result = validate_workbook(path, expected_counts=dict(SYNTHETIC_COUNTS, Jobs=1))
    by_id = {e["id"]: e for e in result.canonical_entities}
    assert by_id[PUBLISHER_ENTITY]["entity_type"] == "company"


def test_product_only_reference_is_not_company_evidence(dataset):
    """Rules 4-6: publisher status and entity spelling are never evidence."""
    assert dataset.report["company_evidence_entities"] == 1
    company_ids = [e["id"] for e in dataset.canonical_entities if e["entity_type"] == "company"]
    assert company_ids == [COMPANY_ENTITY]


def test_other_is_supported_by_the_existing_model_vocabulary():
    """Rule 7: confirm 'other' is in the documented column vocabulary."""
    assert "other" in SUPPORTED_ENTITY_TYPES
    assert "company" in SUPPORTED_ENTITY_TYPES
    column = CanonicalEntity.__table__.columns["entity_type"]
    assert column.type.length == 30
    assert all(len(value) <= 30 for value in SUPPORTED_ENTITY_TYPES)


def test_unsupported_entity_type_is_refused_not_invented(monkeypatch, workbook):
    """Rule 9: an unrepresentable type must stop the import, not be guessed."""
    import dashboard.importer.validate as module

    monkeypatch.setattr(module, "ENTITY_TYPE_OTHER", "unknown-publisher")
    with pytest.raises(EntityTypeUnsupportedError, match="not supported by"):
        validate_workbook(workbook, expected_counts=SYNTHETIC_COUNTS)


def test_overlong_entity_type_is_refused(monkeypatch, workbook):
    import dashboard.importer.validate as module

    long_value = "x" * 40
    monkeypatch.setattr(module, "ENTITY_TYPE_OTHER", long_value)
    monkeypatch.setattr(module, "SUPPORTED_ENTITY_TYPES", frozenset({"company", long_value}))
    with pytest.raises(EntityTypeUnsupportedError, match="exceeds the 30-character column width"):
        validate_workbook(workbook, expected_counts=SYNTHETIC_COUNTS)


# --------------------------------------------------------------------------
# 10. Alias non-fabrication
# --------------------------------------------------------------------------


def test_validation_creates_no_alias_rows(dataset):
    assert dataset.counts()["entity_aliases"] == 0
    assert dataset.report["aliases_imported"] == 0
    assert "exports no entity_aliases" in dataset.report["alias_limitation"]


@pytest.mark.asyncio
async def test_import_leaves_entity_aliases_empty(sessions, dataset):
    async with sessions() as session:
        result = await import_dataset(session, dataset, replace=True)
    assert result.aliases_created == 0
    assert result.final_counts["entity_aliases"] == 0
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(EntityAlias)) == 0
    # But the alias/fuzzy decisions themselves are preserved as audit records.
    async with sessions() as session:
        methods = (await session.execute(select(EntityMappingLog.method))).scalars().all()
    assert methods.count("alias") == 1
    assert methods.count("fuzzy") == 1


# --------------------------------------------------------------------------
# 11. Mapping-log preservation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mapping_log_is_preserved_verbatim(sessions, dataset, rows):
    async with sessions() as session:
        await import_dataset(session, dataset, replace=True)
    async with sessions() as session:
        stored = {
            row.id: row
            for row in (await session.execute(select(EntityMappingLog))).scalars()
        }
    assert len(stored) == len(rows["Entity Mapping Log"])
    for source in rows["Entity Mapping Log"]:
        record = stored[UUID(source["id"])]
        assert record.raw_name == source["raw_name"]
        assert record.normalized_name == source["normalized_name"]
        assert record.canonical_name == source["canonical_name"]
        assert str(record.canonical_entity_id) == source["canonical_entity_id"]
        assert record.method == source["method"]
        assert record.confidence == pytest.approx(source["confidence"])
        assert record.source_url == source["source_url"]
        stamp = record.created_at
        if stamp.tzinfo is None:  # SQLite drops tzinfo; the values are UTC
            stamp = stamp.replace(tzinfo=timezone.utc)
        assert stamp == datetime.fromisoformat(source["created_at"])


def test_unknown_mapping_method_is_rejected(tmp_path, rows):
    rows["Entity Mapping Log"][1]["method"] = "vibes"
    path = write_workbook(tmp_path / "badmethod.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="unknown mapping method"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


# --------------------------------------------------------------------------
# 12-13. Raw documents and provenance
# --------------------------------------------------------------------------


def test_raw_documents_are_deduplicated_by_id(dataset):
    assert len(dataset.raw_documents) == 4
    assert {d["id"] for d in dataset.raw_documents} == {DOC_STARTUP, DOC_PRODUCT, DOC_PAPER, DOC_NEWS}


@pytest.mark.asyncio
async def test_raw_documents_preserve_every_provenance_field(sessions, dataset):
    async with sessions() as session:
        await import_dataset(session, dataset, replace=True)
    async with sessions() as session:
        document = await session.get(RawDocument, DOC_NEWS)
    assert document.source_name == "techcrunch_ai_rss"
    assert document.source_url == "https://techcrunch.com/2026/09/11/example/"
    assert document.canonical_url == "https://techcrunch.com/2026/09/11/example/"
    assert document.http_status == 200
    assert document.content_hash == "d" * 64
    assert document.raw_content_location is None
    assert document.extraction_status == "extracted"
    assert document.publication_date_candidates == {}
    assert document.crawl_run_id is None


@pytest.mark.asyncio
async def test_domain_rows_keep_their_raw_document_reference(sessions, dataset):
    async with sessions() as session:
        await import_dataset(session, dataset, replace=True)
    async with sessions() as session:
        startup = await session.get(Startup, STARTUP_ID)
        news = await session.get(News, NEWS_ID)
    assert startup.raw_document_id == DOC_STARTUP
    assert news.raw_document_id == DOC_NEWS


def test_raw_document_id_must_match_provenance_id(tmp_path, rows):
    rows["Startups"][0]["raw_document_id"] = str(uuid4())
    path = write_workbook(tmp_path / "mismatch.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="does not match provenance_id"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_conflicting_provenance_group_is_rejected(tmp_path, rows):
    """Two rows sharing a raw-document id must export identical provenance."""
    rows["Products"][0].update(_provenance(DOC_STARTUP, "other_source", "https://elsewhere.example", "e" * 64, "parsed"))
    rows["Products"][0]["raw_document_id"] = str(DOC_STARTUP)
    path = write_workbook(tmp_path / "conflict.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="exported inconsistently"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_duplicate_content_hash_is_rejected(tmp_path, rows):
    rows["Products"][0]["provenance_content_hash"] = "a" * 64  # collides with the startup doc
    path = write_workbook(tmp_path / "hash.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="uq_raw_documents_content_hash"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_crawl_run_reference_is_refused_rather_than_invented(tmp_path, rows):
    rows["Startups"][0]["provenance_crawl_run_id"] = str(uuid4())
    path = write_workbook(tmp_path / "crawlrun.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="Refusing to invent a crawl_runs parent row"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


@pytest.mark.asyncio
async def test_import_creates_no_crawl_runs(sessions, dataset):
    async with sessions() as session:
        await import_dataset(session, dataset, replace=True)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(CrawlRun)) == 0


# --------------------------------------------------------------------------
# 14. JSON parsing
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_json_columns_round_trip(sessions, dataset):
    async with sessions() as session:
        await import_dataset(session, dataset, replace=True)
    async with sessions() as session:
        product = await session.get(Product, PRODUCT_ID)
        paper = await session.get(ResearchPaper, PAPER_ID)
    assert product.metadata_json == {"likes": 7, "sdk": "gradio"}
    assert paper.authors == ["ada lovelace", "alan turing"]


def test_malformed_json_is_rejected(tmp_path, rows):
    rows["Products"][0]["metadata_json"] = "{not json"
    path = write_workbook(tmp_path / "badjson.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="not parseable JSON"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_json_of_the_wrong_shape_is_rejected(tmp_path, rows):
    rows["Research Papers"][0]["authors"] = json.dumps({"name": "ada"})  # dict, expected list
    path = write_workbook(tmp_path / "shape.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="expected list"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_naive_timestamp_is_rejected(tmp_path, rows):
    rows["Startups"][0]["collected_at"] = "2026-09-11T09:00:00"
    path = write_workbook(tmp_path / "naive.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="timezone-naive"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_negative_employee_count_is_rejected(tmp_path, rows):
    rows["Startups"][0]["employee_count"] = -3
    path = write_workbook(tmp_path / "neg.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="below the allowed minimum"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_unknown_pricing_model_is_rejected(tmp_path, rows):
    rows["Products"][0]["pricing_model"] = "CHEAP"
    path = write_workbook(tmp_path / "pricing.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="pricing_model_enum"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


# --------------------------------------------------------------------------
# 15. News continuation reconstruction
# --------------------------------------------------------------------------


def test_news_continuation_columns_rejoin_losslessly(workbook, dataset):
    header, read = read_sheet(workbook, "News")
    assert "full_text__part_2" in header  # the fixture really is split
    assert read[0]["full_text"] == NEWS_BODY
    record = dataset.news[0]
    assert record["extracted_metadata"] == NEWS_METADATA
    assert record["extracted_metadata"]["full_text"] == NEWS_BODY
    assert record["full_text_location"] == "inline:extracted_metadata.full_text"


def test_news_continuation_mismatch_is_rejected(tmp_path, rows):
    """Dropping a full_text part must fail, not import a truncated article."""
    rows["News"][0]["full_text__part_2"] = None
    path = write_workbook(tmp_path / "newsbad.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="does not match"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


def test_non_contiguous_continuation_parts_are_rejected(tmp_path, rows):
    news = rows["News"][0]
    news["full_text__part_3"] = news.pop("full_text__part_2")
    path = write_workbook(tmp_path / "gap.xlsx", rows)
    with pytest.raises(WorkbookValidationError, match="not contiguous"):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


@pytest.mark.asyncio
async def test_news_full_text_is_stored_in_extracted_metadata(sessions, dataset):
    async with sessions() as session:
        await import_dataset(session, dataset, replace=True)
    async with sessions() as session:
        news = await session.get(News, NEWS_ID)
    assert news.extracted_metadata["full_text"] == NEWS_BODY
    assert news.full_text_location == "inline:extracted_metadata.full_text"


# --------------------------------------------------------------------------
# 16. Zero jobs
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jobs_remain_exactly_zero(sessions, dataset):
    assert dataset.jobs == []
    async with sessions() as session:
        result = await import_dataset(session, dataset, replace=True)
    assert result.inserted["jobs"] == 0
    assert result.final_counts["jobs"] == 0
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == 0


# --------------------------------------------------------------------------
# 17. Transaction rollback
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_midway_rolls_back_the_entire_import(sessions, dataset, monkeypatch):
    """An error after some tables are written must leave the DB untouched."""
    import dashboard.importer.loader as loader

    real_upsert = loader._upsert
    calls = {"n": 0}

    async def exploding(session, model, records):
        calls["n"] += 1
        if model is News:
            raise RuntimeError("injected integrity failure")
        return await real_upsert(session, model, records)

    monkeypatch.setattr(loader, "_upsert", exploding)

    async with sessions() as session:
        with pytest.raises(RuntimeError, match="injected integrity failure"):
            await import_dataset(session, dataset, replace=True)

    assert calls["n"] > 1  # we really did get past the first table
    async with sessions() as session:
        counts = await table_counts(session)
    assert set(counts.values()) == {0}, counts


@pytest.mark.asyncio
async def test_rollback_also_restores_rows_deleted_by_replace(sessions, dataset, monkeypatch):
    """A failed --replace must not leave the previous dataset destroyed."""
    demo_entity = CanonicalEntity(
        id=uuid4(),
        canonical_name="DreamRP",
        normalized_name="dreamrp",
        entity_type="company",
        created_at=T0,
    )
    async with sessions() as session:
        session.add(demo_entity)
        await session.commit()

    import dashboard.importer.loader as loader

    async def exploding(session, model, records):
        raise RuntimeError("injected failure after delete")

    monkeypatch.setattr(loader, "_upsert", exploding)

    async with sessions() as session:
        with pytest.raises(RuntimeError):
            await import_dataset(session, dataset, replace=True)

    async with sessions() as session:
        survivors = (await session.execute(select(CanonicalEntity.normalized_name))).scalars().all()
    assert survivors == ["dreamrp"]


def test_validation_failure_needs_no_database_at_all(tmp_path, rows):
    """Validation is DB-free by construction: it never imports a session."""
    rows["Startups"][0]["id"] = "nope"
    path = write_workbook(tmp_path / "nodb.xlsx", rows)
    with pytest.raises(WorkbookValidationError):
        validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)


# --------------------------------------------------------------------------
# 18. Idempotency
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_import_does_not_duplicate_anything(sessions, dataset):
    async with sessions() as session:
        first = await import_dataset(session, dataset, replace=True)
    async with sessions() as session:
        second = await import_dataset(session, dataset, replace=False)

    assert second.final_counts == first.final_counts
    assert set(second.inserted.values()) == {0}
    assert second.updated["entity_mapping_log"] == len(dataset.mapping_log)
    assert second.deleted == {}

    async with sessions() as session:
        ids = (await session.execute(select(RawDocument.id))).scalars().all()
        assert len(ids) == len(set(ids)) == len(dataset.raw_documents)
        startup = await session.get(Startup, STARTUP_ID)
        assert startup.canonical_entity_id == COMPANY_ENTITY
        entity = await session.get(CanonicalEntity, PUBLISHER_ENTITY)
        assert entity.entity_type == "other"
        stamp = entity.created_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        assert stamp == T0 + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_third_import_with_replace_is_also_stable(sessions, dataset):
    async with sessions() as session:
        await import_dataset(session, dataset, replace=True)
    async with sessions() as session:
        again = await import_dataset(session, dataset, replace=True)
    assert again.final_counts == dataset.counts()


# --------------------------------------------------------------------------
# 19. Safe replacement / reset behaviour
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_clears_demo_rows_in_the_owned_tables(sessions, dataset):
    demo_entity_id = uuid4()
    async with sessions() as session:
        session.add(
            CanonicalEntity(
                id=demo_entity_id,
                canonical_name="DreamRP",
                normalized_name="dreamrp",
                entity_type="company",
                created_at=T0,
            )
        )
        session.add(
            Startup(
                id=uuid4(),
                entity_name="DreamRP",
                canonical_entity_id=demo_entity_id,
                source_name="demo",
                source_url="https://demo.example/dreamrp",
                collected_at=T0,
            )
        )
        session.add(EntityAlias(id=uuid4(), canonical_entity_id=demo_entity_id, alias="Dream RP", normalized_alias="dream rp"))
        await session.commit()

    async with sessions() as session:
        result = await import_dataset(session, dataset, replace=True)

    assert result.deleted["startups"] == 1
    assert result.deleted["canonical_entities"] == 1
    assert result.deleted["entity_aliases"] == 1
    assert result.final_counts == dataset.counts()
    async with sessions() as session:
        names = (await session.execute(select(CanonicalEntity.normalized_name))).scalars().all()
    assert "dreamrp" not in names


@pytest.mark.asyncio
async def test_default_import_deletes_nothing(sessions, dataset):
    """Without --replace the importer is purely additive/upserting."""
    demo_entity_id = uuid4()
    async with sessions() as session:
        session.add(
            CanonicalEntity(
                id=demo_entity_id,
                canonical_name="DreamRP",
                normalized_name="dreamrp",
                entity_type="company",
                created_at=T0,
            )
        )
        await session.commit()

    async with sessions() as session:
        result = await import_dataset(session, dataset, replace=False)

    assert result.deleted == {}
    assert result.final_counts["canonical_entities"] == len(dataset.canonical_entities) + 1
    async with sessions() as session:
        assert await session.get(CanonicalEntity, demo_entity_id) is not None


@pytest.mark.asyncio
async def test_replace_never_touches_unrelated_tables(sessions, dataset):
    """sources / crawl_runs (and friends) must survive a --replace import."""
    source_id, run_id = uuid4(), uuid4()
    async with sessions() as session:
        session.add(
            Source(
                id=source_id,
                name="demo_source",
                vertical="news",
                base_url="https://demo.example",
                discovery_mechanism="rss",
                created_at=T0,
            )
        )
        session.add(CrawlRun(id=run_id, vertical="news", started_at=T0, status="completed", stats={}))
        await session.commit()

    async with sessions() as session:
        await import_dataset(session, dataset, replace=True)

    async with sessions() as session:
        assert await session.get(Source, source_id) is not None
        assert await session.get(CrawlRun, run_id) is not None


def test_reset_scope_is_declared_and_disjoint_from_untouched_tables():
    reset_tables = {label for label, _ in RESET_ORDER}
    assert reset_tables == {
        "canonical_entities",
        "entity_aliases",
        "entity_mapping_log",
        "raw_documents",
        "startups",
        "products",
        "research_papers",
        "jobs",
        "news",
    }
    assert reset_tables.isdisjoint(UNTOUCHED_TABLES)


def test_reset_order_is_child_before_parent():
    """canonical_entities/raw_documents must be deleted after their children."""
    order = [label for label, _ in RESET_ORDER]
    for child in ("startups", "products", "research_papers", "jobs", "news", "entity_mapping_log"):
        assert order.index(child) < order.index("canonical_entities")
        assert order.index(child) < order.index("raw_documents")
    assert order.index("entity_aliases") < order.index("canonical_entities")


# --------------------------------------------------------------------------
# The workbook itself must never be modified
# --------------------------------------------------------------------------


def test_reader_does_not_modify_the_workbook(tmp_path, rows):
    path = write_workbook(tmp_path / "immutable.xlsx", rows)
    before = (tmp_path / "immutable.xlsx").read_bytes()
    validate_workbook(path, expected_counts=SYNTHETIC_COUNTS)
    assert (tmp_path / "immutable.xlsx").read_bytes() == before
    # And it is still openable as the same six-tab workbook.
    book = load_workbook(path, read_only=True)
    try:
        assert tuple(book.sheetnames) == SHEET_ORDER
    finally:
        book.close()
