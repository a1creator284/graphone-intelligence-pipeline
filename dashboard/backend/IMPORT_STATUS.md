# Task 4: real-workbook import — implemented

Supersedes the 2026-09-11 "blocked preflight checkpoint" that previously lived
in this file. The blocker recorded there was the missing `entity_type`
evidence; it has been resolved by an **explicitly approved, documented
application policy** rather than by guessing a type. The importer now exists.

Full documentation: **[`dashboard/importer/README.md`](../importer/README.md)**.

## What exists now

| Path | Purpose |
| --- | --- |
| `dashboard/importer/workbook.py` | Read-only XLSX reader; lossless `__part_N` continuation rejoin; strict cell coercion. |
| `dashboard/importer/validate.py` | Whole-workbook validation before any DB access; canonical reconstruction; `entity_type` policy. |
| `dashboard/importer/loader.py` | Single-transaction upsert; bounded replace/reset. |
| `dashboard/importer/cli.py` | `python -m dashboard.importer`. |
| `tests/test_dashboard_importer.py` | 56 tests; synthetic workbook + in-memory SQLite. |

No changes were made to `src/`, to any assessment artifact, or to
`submission/graphone_final.xlsx`.

## Exact local import command (on the Mac, from the repo root)

```bash
export DATABASE_URL='postgresql+asyncpg://graphone:graphone@localhost:5432/graphone'
python -m dashboard.importer --validate-only   # dry run; opens no DB connection
python -m dashboard.importer --replace         # real import, one transaction
```

## How the previous blocker was resolved

`CanonicalEntity.entity_type` is `String(30)` documented as `company|other`,
and the workbook exports no authoritative type. Rather than inferring a type
from a name or treating every publisher as a company, the importer applies a
deterministic evidence-only policy and labels it as a dashboard/application
classification, **not** source-provided metadata:

1. referenced by ≥ 1 Startup → `company`
2. referenced by ≥ 1 Job → `company`
3. otherwise → `other`

A name's spelling is never inspected, and being a Hugging Face / OpenRouter
publisher is never company evidence (`Product.startup_name` is the
vendor/publisher, which is exactly why a Product reference does not qualify).
`other` was confirmed to be inside the model's documented vocabulary, so no new
value is introduced; if the policy ever produced an unrepresentable value the
import raises `EntityTypeUnsupportedError` and stops.

**IndexTeam** (`3953682d-497b-4b4d-bba2-d8ae6dc2456a`) has only a Product
reference, so under this policy it is **`other`**. A test pins that exact
canonical ID.

Resulting distribution on the real workbook: **997 `company`, 352 `other`**
(1349 canonical entities total).

## Database environment

The Mac PostgreSQL (`graphone@localhost:5432/graphone`) is still **not**
reachable from the coding-agent sandbox, so the real import was not executed
against it from here and no claim is made that it was. The importer was instead
exercised end-to-end against an isolated SQLite database using the real
workbook, twice, proving the loader and its idempotency. The user runs the
command above locally to populate PostgreSQL.

No replacement database was initialised and described as the verified one.

## Read-only workbook checks (unchanged, now enforced in code)

Input: `submission/graphone_final.xlsx`,
SHA-256 `2270cbbff3ed92c6a991e12ad6d17326ddc5462d162e3c347803b9ba5645b5d9`.

| Sheet (exact order) | Records |
| --- | ---: |
| Startups | 1000 |
| Products | 1000 |
| Research Papers | 1000 |
| Jobs | 0 |
| News | 45 |
| Entity Mapping Log | 2000 |

The checks that were previously ad hoc are now part of
`dashboard/importer/validate.py` and run before every import: exact sheets and
order, required headers (covering every ORM column), row counts, UUID validity,
duplicate IDs, every declared unique constraint, timezone-aware timestamps,
JSON shape, News continuation agreement, raw-document/provenance consistency,
refusal of crawl-run references, and all foreign keys.

Mapping methods: `created` 1349, `normalized_exact` 641, `alias` 7, `fuzzy` 3.
88 distinct raw documents, each domain `raw_document_id` equal to its
`provenance_id`, with repeated provenance groups agreeing on all eleven fields.

No workbook contents were corrected, normalized or overwritten.

## Behaviour summary

* **Validation first.** Validation imports no database session at all, so a
  failure performs zero writes structurally. Exit code `2`.
* **One transaction.** Replace + all nine tables in a single `session.begin()`.
  Any error rolls back everything, including the replace deletion. Exit code
  `1`. Two tests prove it.
* **Idempotent.** Keyed on the workbook's own primary keys; a second run
  inserts 0 rows and leaves counts, IDs and relationships identical.
* **Bounded reset.** `--replace` deletes rows (never `TRUNCATE`) from only
  `news, jobs, research_papers, products, startups, entity_mapping_log,
  entity_aliases, raw_documents, canonical_entities`, child-first. It never
  touches `sources`, `crawl_runs`, `crawl_jobs`, `llm_requests` or
  `processing_errors`, and creates/drops/alters no tables. Without `--replace`
  nothing is deleted at all.
* **`created_at`** is the earliest explicit mapping-log timestamp per canonical
  ID, never import time.

## Documented limitations (not worked around)

* **`entity_aliases`: 0 rows imported.** The workbook exports no alias rows and
  no authoritative alias IDs, so none are created. The 7 `alias` and 3 `fuzzy`
  decisions remain preserved in `entity_mapping_log` as audit records. Aliases
  were **not** imported; the entity view will show `alias_count = 0`.
* **`crawl_runs`: 0 rows created**; `raw_documents.crawl_run_id` stays NULL. A
  populated crawl-run reference fails validation rather than inventing a parent.
* **`raw_content_location`** imported exactly as exported (NULL for all 88). No
  raw payload is invented or fetched.
* **`full_text` / `full_text_status`** are export-only columns; the body lives
  in `News.extracted_metadata`, as the ORM stores it.

## Expected counts after `--replace`

`canonical_entities` 1349 · `entity_aliases` 0 · `entity_mapping_log` 2000 ·
`raw_documents` 88 · `startups` 1000 · `products` 1000 ·
`research_papers` 1000 · `jobs` **0** · `news` 45.

Jobs remain exactly zero because the workbook contains zero Jobs. No dataset
was padded.
