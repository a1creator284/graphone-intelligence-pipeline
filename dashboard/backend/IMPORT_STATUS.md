# Task 4: real-workbook import — blocked preflight checkpoint

Inspected on 2026-09-11 against dashboard baseline
`5c594ecd03eef677da9ff74f8f9fd680e3641270`.

**No importer implemented and no database writes performed.** The requested
stop-on-ambiguity rule was triggered. This is an inspection checkpoint, not a
successful import or a complete validation suite.

## Blocking entity-type ambiguity

The dashboard reuses `src/storage/models.py`; it has no separate ORM schema.
`CanonicalEntity.entity_type` is a String(30), with documented semantics
`company|other` and a default of `company`. The resolver also defaults to
`company` (`src/resolution/resolver.py:95,253`). That default does not establish
a publisher's real type.

`Product.startup_name` means vendor/publisher, not necessarily a startup.
`src/crawlers/huggingface_products.py:40,306-313` explicitly derives it from
an organization/user author or repository owner namespace. The product
pipeline resolves that publisher (`src/pipeline/products.py:264-271`).
Neither the workbook's product headers nor its mapping-log headers contain
an authoritative entity type.

A concrete unresolved case:

- Canonical entity ID: `3953682d-497b-4b4d-bba2-d8ae6dc2456a`
- Canonical name: `IndexTeam`
- Explicit normalized name: `indexteam`
- Product ID: `01f28282-1bcb-42bf-b20a-a1c14f5c80f8`
- Product source: `huggingface_spaces`
- Product URL: https://huggingface.co/spaces/IndexTeam/IndexTTS-2-Demo
- Mapping-log ID: `c3d09fe3-fd94-4043-83bc-01ef8f471e4f`
- Mapping method: `created`
- Mapping timestamp: `2026-09-11T09:27:54.186700+00:00`
- No linked Startup record. Product metadata contains hub ID, creation and
  modification timestamps, license, likes, SDK, and tags, but no owner type.

This evidence establishes a publisher namespace, not `company` versus
`other`. Inferring type from its spelling, treating every publisher as a
company, or silently interpreting `other` as unknown would be a guess.

There are 997 distinct Startup-linked canonical IDs and 353 Product-linked
canonical IDs, with one shared ID: 352 IDs are product-only. That is a
relationship distribution, **not a verified entity-type distribution**.
It does not claim that every product-only entity has the same ambiguity.
The IndexTeam case alone triggers the mandatory stop.

To resume, provide authoritative type evidence keyed by canonical entity ID
(for example the original canonical-entity export), or explicitly approve a
documented application policy for otherwise untyped publishers. Additional
entities may still need authoritative evidence; no types have been assigned.

## Database environment blocker

The requested target is PostgreSQL `graphone`, user `graphone`,
`localhost:5432`. In this sandbox a TCP check returned connection refused
(error 111). No DATABASE_URL/PostgreSQL environment configuration was present,
and the PostgreSQL client/server executables were not available on the
checked paths. Therefore the **live database schema, demo state, and counts
could not be inspected**. Model source was inspected, not the live schema.

Do not initialize a replacement database and describe it as the previously
verified database. Restore access to the intended instance and supply its
connection configuration through the environment before proceeding.

## Read-only workbook checks completed

Input: `submission/graphone_final.xlsx`

SHA-256:
`2270cbbff3ed92c6a991e12ad6d17326ddc5462d162e3c347803b9ba5645b5d9`

An ad hoc Python/openpyxl assertion pass, without writing files, verified:

| Sheet (exact order) | Records |
| --- | ---: |
| Startups | 1000 |
| Products | 1000 |
| Research Papers | 1000 |
| Jobs | 0 |
| News | 45 |
| Entity Mapping Log | 2000 |

- No duplicate headers or record IDs within a sheet; `id` and
  `export_as_of_utc` headers present. Complete schema/header validation is
  still required; this was not a substitute for the requested test suite.
- All populated record, canonical, raw-document, provenance and crawl-run ID
  fields checked as UUIDs; populated timestamps checked as timezone-aware
  ISO timestamps.
- All domain canonical references resolve to explicit mapping records.
- 1,349 canonical IDs, each with exactly one explicit `created` mapping row;
  nonempty canonical/normalized names on that row and consistent canonical
  names across its mapping records.
- Mapping methods: created=1349, normalized_exact=641, alias=7, fuzzy=3.
- 88 distinct raw-document IDs; each domain raw_document_id matches its
  provenance_id; repeated provenance records agree across all exported
  provenance fields.
- 5,090 JSON cells parsed as the expected object/list shape, after joining
  continuation columns in numeric order where present.
- All 45 News full_text values, reassembled from continuation columns,
  exactly match the full_text embedded in reassembled extracted_metadata.

No workbook contents were corrected, normalized, or overwritten.

## Required import behavior once unblocked (not implemented)

- Validate the complete workbook before any DB mutation: exact six sheets,
  required headers, expected counts, IDs, timestamps, safe JSON, continuation
  integrity, foreign keys, uniqueness, schema compatibility and entity types.
- Use the explicit `created` mapping row for canonical_name and
  normalized_name, preserving its canonical_entity_id. Other mapping rows
  describe input-name normalization, which can differ for aliases/fuzzy hits.
  Use the earliest explicit mapping created_at for each canonical ID, never
  import time. Preserve every one of the 2,000 mapping rows unchanged.
- Reconstruct RawDocument rows from explicit provenance fields and original
  IDs, preserving source/page distinctions. Do not invent crawl-run parents
  or raw payload files. Handle any unsupported references by failing safely.
- Keep exact domain IDs, raw_document_id, canonical_entity_id, sources,
  metadata and timestamps. Rejoin News continuation strings before parsing
  JSON, and verify full-text agreement. Do not fetch local full-text paths.
- The workbook has no authoritative EntityAlias rows or alias IDs. Leave
  aliases absent on a clean target; do not derive invented aliases from the
  seven alias decisions or three fuzzy decisions. Audit decisions remain.
- Inspect actual DB data before choosing a reset scope. Do not silently
  delete DreamRP or unrelated records, mix the final dataset with demo rows,
  or truncate tables. Any replacement needs explicit operator-approved scope
  and must include all changes in a single rollback-capable transaction.
- Add focused automated tests for all requested validation cases, ambiguous
  type rejection, alias non-fabrication, mapping/provenance fidelity, zero
  Jobs, injected transaction failure, unrelated-data protection and a second
  import producing identical contents without duplicates.

**Exact import command:** unavailable; no importer exists at this checkpoint.
Do not run the assessment pipeline as an import substitute. Document the
actual command and replacement authorization once implemented and tested.

**DB counts/type distribution after import:** unavailable; no import occurred.
**Rollback and idempotency:** not run and not claimed. No transaction started.

Only this dashboard document is part of the checkpoint. No changes to `src/`,
assessment artifacts, submission files, database files or generated outputs
are included. No merge or push to `genspark_ai_developer` is authorized.
