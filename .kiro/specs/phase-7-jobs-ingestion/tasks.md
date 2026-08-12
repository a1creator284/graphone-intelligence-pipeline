# Tasks Document: Phase 7 - Jobs Ingestion

## Overview
This document breaks down the implementation of Phase 7 (Jobs Ingestion) into independently verifiable tasks. All tasks track towards fulfilling the requirements defined in `requirements.md` using the architecture specified in `design.md`.

## Tasks

- [x] 1. Set up jobs vertical infrastructure and database schema
  - [x] 1.1 Update `Job` model in `src/storage/models.py` to add `raw_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("raw_documents.id"), nullable=True)`.
  - [x] 1.2 Add `JobRecord` schema to `src/validation/schemas.py` including `raw_document_id`.
  - [x] 1.3 Create `JobRepository` in `src/storage/repositories.py` with idempotent `upsert_job` method.
  - [x] 1.4 Create `src/pipeline/jobs.py` with `run_jobs_pipeline()` function.
  - [x] 1.5 Update `src/main.py` CLI to route `--vertical jobs` to `run_jobs_pipeline()`.

- [x] 2. Implement JSON API adapters (RemoteOK and WorkingNomads)
  - [x] 2.1 Create `src/crawlers/remoteok.py` adapter. Map `position`, `company`, `url`, `date`.
  - [x] 2.2 Create `src/crawlers/workingnomads.py` adapter. Map `title`, `company_name`, `url`, `pub_date`.
  - [x] 2.3 Write mocked unit tests for both JSON API adapters (validates successful parsing and date extraction).

- [x] 3. Implement YCombinator Ask HN Adapter
  - [x] 3.1 Create `src/crawlers/ycombinator_jobs.py` adapter utilizing Algolia search for "Ask HN: Who is hiring?".
  - [x] 3.2 Implement heuristic extraction for `title` and `company` from the comment string.
  - [x] 3.3 Write mocked unit tests for Algolia Thread/Comment extraction and heuristics.

- [x] 4. Implement Sitemap & JSON-LD adapters (Wellfound and BuiltIn)
  - [x] 4.1 Create `src/crawlers/sitemap_jobs_base.py` for shared XML sitemap fetching logic.
  - [x] 4.2 Create `src/crawlers/wellfound.py` and `src/crawlers/builtin.py` extending the base class.
  - [x] 4.3 Implement `JobPosting` JSON-LD extraction to parse `title`, `hiringOrganization.name`, `datePosted`.
  - [x] 4.4 Write mocked unit tests for XML Sitemap parsing and HTML JSON-LD extraction.

- [x] 5. Implement jobs pipeline orchestration
  - [x] 5.1 Wire all five adapters in `run_jobs_pipeline()`.
  - [x] 5.2 Implement provenance preservation: fetch payload, persist to `RawDocumentRepository`, and capture the returned `raw_document_id`.
  - [x] 5.3 Implement freshness filtering using existing `is_fresh()` utility on the `posted_at` field.
  - [x] 5.4 Implement integration of Validation (via `JobRecord` with `raw_document_id`) and Persistence (via `JobRepository`).
  - [x] 5.5 Ensure proper structured logging matches the Phase 6 patterns (`pipeline_start`, `validation_failed`, etc.).
  - [x] 5.6 Write an integration test for the full jobs pipeline with mocked adapters, explicitly asserting that `raw_document_id` is linked.

- [ ] 6. Final checkpoint & Verification
  - [ ] 6.1 Ensure the full test suite runs without live network dependencies (`pytest -q`).
  - [ ] 6.2 Verify CLI end-to-end simulated run (`python -m src.main --vertical jobs --dry-run`).
  - [ ] 6.3 Update the main README to document the Jobs vertical features and architecture.

## Dependencies
- Tasks in section 2, 3, and 4 are independent and can be parallelized.
- Task 5 depends on the completion of Tasks 1-4.
- Task 6 depends on all previous tasks.
