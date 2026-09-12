# Dashboard importer — final assessment workbook → PostgreSQL

Loads the real assessment dataset from `submission/graphone_final.xlsx` into
the PostgreSQL database the personal dashboard reads, reusing the ingestion
pipeline's own ORM (`src/storage/models.py`). Nothing in `src/` is modified,
and the workbook is opened strictly read-only.

## Location

| File | Purpose |
| --- | --- |
| `dashboard/importer/workbook.py` | Read-only XLSX reader. Rejoins the exporter's `<column>__part_N` continuation columns losslessly; strict UUID / timestamp / JSON / int / bool coercion. |
| `dashboard/importer/validate.py` | Whole-workbook validation **before any database access**; rebuilds canonical entities; applies the `entity_type` policy. |
| `dashboard/importer/loader.py` | Single-transaction upsert; bounded replace/reset. |
| `dashboard/importer/cli.py` | `python -m dashboard.importer` entry point. |
| `tests/test_dashboard_importer.py` | 56 tests, synthetic workbook + in-memory SQLite. No live PostgreSQL needed. |

## Exact local import command (run on the Mac, from the repo root)

```bash
cd ~/graphone-intelligence-pipeline           # or wherever the repo lives
source .venv/bin/activate                     # the env that has the requirements installed

# 0. Point at the local database (the pipeline default is already this URL,
#    so this line is only needed if your .env overrides it).
export DATABASE_URL='postgresql+asyncpg://graphone:graphone@localhost:5432/graphone'

# 1. DRY RUN — validates all six sheets and opens NO database connection.
python -m dashboard.importer --validate-only

# 2. REAL IMPORT — replaces the DreamRP demo dataset with the final dataset,
#    inside one transaction.
python -m dashboard.importer --replace
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--validate-only` | Validate and exit. No engine is constructed, so no connection is opened. |
| `--replace` | Delete existing rows from the nine importer-owned tables first (see *Reset / replace* below). Without it, the importer is purely upserting and deletes nothing. |
| `--workbook PATH` | Import a different six-tab export (default `submission/graphone_final.xlsx`). |
| `--database-url URL` | Override `DATABASE_URL` for one run. |
| `--skip-count-check` | Do not assert the verified per-sheet row counts (for non-final workbooks). |
| `--json` | Machine-readable report on stdout. |

Exit codes: `0` success, `2` validation failed (**zero** database writes
attempted), `1` import failed (transaction rolled back).

> The database is **not** reachable from the coding-agent sandbox, so the real
> import was never executed against the Mac instance from here. It was
> exercised end-to-end against an isolated SQLite database using the real
> workbook; see *Verified run* below.

## Validation behaviour

`validate_workbook()` materialises and cross-checks the entire workbook and
returns an in-memory dataset. It never imports a database session, so a
validation failure cannot have written anything — that property is structural,
not a promise. Checks:

* exactly the six sheet names `Startups, Products, Research Papers, Jobs, News,
  Entity Mapping Log`, **in that order** — an extra, missing or reordered sheet
  fails;
* every required header per sheet (the header sets cover every ORM column, and
  a test asserts that, so a future ORM column cannot be silently skipped);
* expected row counts (`1000/1000/1000/0/45/2000`), pinned in
  `EXPECTED_SHEET_COUNTS`;
* every ID field parses as a UUID; record IDs are unique within a sheet;
* every unique constraint the ORM declares is pre-checked in Python
  (`uq_startups_source_url`, `uq_products_source_url`, `uq_products_dedup_key`,
  `uq_research_papers_paper_url`, `uq_jobs_source_url`, `uq_news_source_url`,
  `uq_raw_documents_content_hash`, `uq_canonical_entities_normalized_name`), so
  a violation is reported as a named constraint rather than a driver error;
* every timestamp is ISO-8601 **and** timezone-aware (a naive timestamp fails);
* every JSON cell parses into its documented container (`dict` for
  `metadata_json` / `extracted_metadata` / `publication_date_candidates`,
  `list` for `authors`) — malformed JSON or the wrong shape fails rather than
  degrading to an empty container;
* `pricing_model` is inside `pricing_model_enum`; `employee_count` and
  `github_stars` satisfy their non-negative check constraints;
* `method` is inside the known mapping vocabulary;
* News continuation columns rejoin contiguously, and the rejoined `full_text`
  equals the `full_text` inside the rejoined `extracted_metadata`;
* on every domain row `raw_document_id == provenance_id`, and rows sharing a
  raw-document ID export byte-identical provenance on all eleven columns;
* every domain/mapping `canonical_entity_id` resolves to a reconstructed
  canonical entity, and every `raw_document_id` to a reconstructed raw document.

## Transaction and rollback behaviour

`import_dataset()` owns one `async with session.begin()` block covering the
optional replace deletion, canonical entities, raw documents, the mapping log
and all five domain tables. Any exception — including a database integrity
error — rolls the whole thing back:

* no partial dataset is left behind;
* a failed `--replace` does **not** leave the previous dataset destroyed (the
  deletes roll back too);
* the CLI reports the failure and returns exit code `1`.

Two tests prove this: an injected failure before the last table leaves all nine
tables at zero, and an injected failure after the replace deletion leaves a
pre-existing demo entity intact.

`DELETE` is used rather than `TRUNCATE` specifically so the reset participates
in the transaction and cannot cascade into an unrelated table.

## Reset / replace behaviour — exactly what it affects

The local database currently holds a small DreamRP demo dataset. The final
dataset is never silently mixed with it: you choose.

* **without `--replace`** — pure upsert. Nothing is deleted. Demo rows survive
  alongside the imported dataset. This is also the idempotent re-run path.
* **with `--replace`** — rows are deleted from exactly these nine tables,
  child-first so foreign keys stay satisfied:

  `news → jobs → research_papers → products → startups → entity_mapping_log →
  entity_aliases → raw_documents → canonical_entities`

  `entity_aliases` is included because it is a child of `canonical_entities`;
  leaving demo aliases behind would break the FK when demo entities are
  removed.

**Never touched by either mode:** `sources`, `crawl_runs`, `crawl_jobs`,
`llm_requests`, `processing_errors`. No table is created, dropped, altered or
truncated — the importer assumes the schema already exists (created by the
pipeline / Alembic).

## Idempotency

Identity comes from the workbook's own primary keys, so a re-run is an update
of identical values, not an insert. After a second import:

* row counts are unchanged and IDs remain unique;
* canonical relationships are unchanged;
* the mapping log stays at 2000 rows;
* no duplicate raw documents are created (88 stays 88).

Tests assert a second `--replace`-less run reports `inserted == 0` for every
table with identical final counts, and that a second `--replace` run is equally
stable.

## Canonical entity reconstruction

The workbook exports no `canonical_entities` sheet. Each canonical entity is
rebuilt from its **single explicit `Entity Mapping Log` `created` row**, which
carries the authoritative `canonical_entity_id`, `canonical_name` and
`normalized_name`. Validation requires exactly one `created` row per canonical
ID (1349 of them), non-empty names on it, and a consistent `canonical_name`
across all that entity's mapping rows. Nothing is fabricated: an entity with
no explicit creation record fails the import instead of being invented.

### `created_at` policy

`created_at` is the **earliest** `Entity Mapping Log.created_at` among all
mapping rows for that canonical ID — never import time. (In this workbook the
`created` row happens to be the earliest for all 1349 entities, but the
importer takes the minimum rather than relying on that.)

## `entity_type` policy — application classification, NOT source metadata

**The workbook does not export `entity_type`.** It is assigned by the dashboard
using this deterministic, evidence-only policy:

1. referenced by ≥ 1 **Startup** row → `company`
2. referenced by ≥ 1 **Job** row → `company`
3. otherwise → `other`

Explicitly **not** evidence: the entity's name or spelling; being a Hugging
Face / OpenRouter publisher; a Product reference (`Product.startup_name` means
vendor/publisher, not necessarily a startup).

`other` is confirmed to be in the vocabulary documented on
`CanonicalEntity.entity_type` (`String(30)  # company|other`), so no new value
is needed. If the policy ever produced a value outside that vocabulary, or one
too wide for the column, validation raises `EntityTypeUnsupportedError` and the
import stops rather than inventing a value.

Under this policy on the real workbook: **997 `company`, 352 `other`**.

### IndexTeam

Canonical ID `3953682d-497b-4b4d-bba2-d8ae6dc2456a` (`IndexTeam`) is referenced
only by a Product (`huggingface_spaces`), never by a Startup or a Job.
It is therefore classified **`other`** — being a Hugging Face publisher is not
treated as company evidence. A dedicated test pins this using that exact
canonical ID.

## Known limitations (documented, not worked around)

* **`entity_aliases`: zero rows imported.** The workbook exports no alias rows
  and no authoritative alias IDs. The importer creates none — it does not
  derive alias rows from the 7 `alias` and 3 `fuzzy` resolution decisions,
  because those decisions record *input* names, not authoritative alias
  records, and their IDs are mapping-log IDs. All 2000 decisions remain
  preserved in `entity_mapping_log` as the audit trail. **Aliases were not
  imported**; the dashboard's entity view will show `alias_count = 0`.
* **`crawl_runs`: zero rows created.** The workbook exports no crawl runs.
  `raw_documents.crawl_run_id` is left NULL (it is NULL throughout this
  workbook anyway). If a future export populated it, validation *fails* rather
  than inventing a parent crawl run.
* **`raw_documents.raw_content_location`** is imported exactly as exported
  (NULL for all 88 documents here). No raw payload bytes are invented, and no
  local full-text file is read or fetched.
* **`full_text` / `full_text_status`** are export-only columns with no ORM
  home; the article body lives inside `News.extracted_metadata`, exactly as the
  pipeline stores it. They are used for validation, not persisted separately.
* **`resolved_canonical_name`** is a denormalised export convenience (a LEFT
  JOIN of the canonical name) and is validated against the reconstructed
  entities rather than stored twice.

## Expected final counts after `--replace`

| Table | Rows |
| --- | ---: |
| `canonical_entities` | 1349 |
| `entity_aliases` | 0 *(limitation above)* |
| `entity_mapping_log` | 2000 |
| `raw_documents` | 88 |
| `startups` | 1000 |
| `products` | 1000 |
| `research_papers` | 1000 |
| `jobs` | **0** — the workbook has zero Jobs; none are fabricated |
| `news` | 45 |

Mapping methods: `created` 1349, `normalized_exact` 641, `alias` 7, `fuzzy` 3.
`entity_type`: `company` 997, `other` 352.

## Verified run

```
$ python -m dashboard.importer --validate-only
workbook: submission/graphone_final.xlsx
validated counts:
  raw_documents          88
  canonical_entities     1349
  entity_aliases         0
  entity_mapping_log     2000
  startups               1000
  products               1000
  research_papers        1000
  jobs                   0
  news                   45
entity_type distribution: {'company': 997, 'other': 352}
mapping methods: {'normalized_exact': 641, 'created': 1349, 'alias': 7, 'fuzzy': 3}

validation OK -- no database connection was opened.
```

The same real workbook was then loaded twice into an isolated SQLite database
(not the Mac PostgreSQL, which is unreachable from the sandbox). The second run
inserted 0 rows, updated every existing row in place, and produced identical
final counts.

## Tests

```bash
python -m pytest tests/test_dashboard_importer.py -q      # 56 passed
```
