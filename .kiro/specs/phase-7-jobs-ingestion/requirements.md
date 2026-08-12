# Requirements Document: Phase 7 - Jobs Ingestion

## Introduction

This specification defines requirements for the Jobs ingestion vertical within the GraphOne Intelligence Pipeline (Phase 7). The system extends the existing async crawler infrastructure to ingest, validate, and deduplicate AI-related job postings from five distinct sources.

## Attributes & Data Model
Based on the existing database schema (`Job` model in `src/storage/models.py`), the following attributes are supported:

**SUPPORTED REQUIREMENTS (Database Columns):**
- `title` (String, required)
- `company` (String, required)
- `url` (String, required)
- `posted_at` (DateTime, required)
- `is_remote` (Boolean, optional)
- `role_family` (String, optional)
- `source_name` (String, required)
- `raw_document_id` (UUID, optional, foreign key to RawDocument)
- `metadata_json` (JSONB, default dict)
- `collected_at` (DateTime)

**DESIGN INFERENCE (Stored in `metadata_json`):**
- `description`: The job description body.
- `employment_type`: e.g., Full-time, Contract.
- `salary_compensation`: Salary bands or compensation details.
- `location`: Geographic location string.

## Requirements

### Requirement 1: Job Source Discovery
**User Story:** As a pipeline operator, I want the system to discover AI job postings from five configured sources.
1. THE Jobs_Pipeline SHALL register five enabled adapters: `remoteok_ai_jobs`, `workingnomads_ai_jobs`, `ycombinator_hn_whoishiring`, `wellfound_ai_jobs`, `builtin_ai_jobs`.
2. FOR `remoteok_ai_jobs` and `workingnomads_ai_jobs`, THE adapter SHALL fetch job listings directly via their official JSON APIs.
3. FOR `ycombinator_hn_whoishiring`, THE adapter SHALL use the Algolia API to find the latest "Ask HN: Who is hiring?" thread and extract top-level comments as job postings.
4. FOR `wellfound_ai_jobs` and `builtin_ai_jobs`, THE adapter SHALL use sitemap discovery to find job URLs, then fetch the HTML pages.

### Requirement 2: Job Parsing and Extraction
**User Story:** As a pipeline operator, I want job details extracted deterministically.
1. THE `remoteok` and `workingnomads` adapters SHALL parse fields directly from the JSON API response.
2. THE `wellfound` and `builtin` adapters SHALL extract the `JobPosting` JSON-LD schema from the fetched HTML to populate job fields.
3. THE `ycombinator` adapter SHALL extract company and title from the first line of the comment text, defaulting to reasonable heuristics without invoking an LLM.
4. IF a required field (`title`, `company`, `url`, `posted_at`) is missing, THE adapter SHALL NOT invent it. The record SHALL be rejected during validation.

### Requirement 3: Publication Date and Freshness
**User Story:** As a pipeline operator, I want only fresh job postings ingested.
1. THE Date_Engine SHALL be used to parse dates (e.g., JSON-LD `datePosted`, API `date` / `pub_date`).
2. THE Jobs_Pipeline SHALL validate `posted_at` using the existing `is_fresh()` utility, ensuring jobs are within the configured freshness window (e.g., 24 hours or 7 days, configurable per run).
3. Stale or future-dated jobs SHALL be rejected and logged identically to Phase 6 News.

### Requirement 4: Deduplication
**User Story:** As a pipeline operator, I want job postings deduplicated so identical jobs are not stored multiple times.
1. THE database SHALL enforce the existing unique constraint on `(source_name, url)`.
2. THE Jobs_Repository SHALL use `INSERT ... ON CONFLICT DO NOTHING` for idempotent upserts.

### Requirement 5: Error Handling & Resilience
**User Story:** As a pipeline operator, I want the pipeline to handle rate limits and blocks gracefully.
1. THE adapters SHALL use the shared `AsyncHttpClient`.
2. Rate limits (429) and blocked sources (403/401) SHALL be handled identically to Phase 6, utilizing `RateLimitError` and `BlockedSourceError`.
3. Independent retry loops SHALL NOT be implemented in the adapters.

### Requirement 6: CLI Integration & Observability
**User Story:** As a pipeline operator, I want to run the jobs pipeline via CLI and observe its performance.
1. THE CLI SHALL support `python -m src.main --vertical jobs`.
2. THE pipeline SHALL emit structured logs matching the Phase 6 patterns (e.g., `pipeline_start`, `fetch_failed`, `validation_failed`, `pipeline_complete`).
3. THE pipeline SHALL report aggregate statistics (discovered, fetched, validated, rejected_stale, persisted, etc.).

### Requirement 7: Testing Strategy
**User Story:** As a developer, I want the jobs vertical fully tested without live network calls.
1. THE test suite SHALL use mocked fixtures (JSON API responses, XML Sitemaps, HTML with JSON-LD).
2. NO live network calls SHALL be made in the default test suite.
3. THE test suite SHALL explicitly verify that the `raw_document_id` linkage is successfully propagated to the database.

### Requirement 8: Raw Content Provenance
**User Story:** As a pipeline operator, I want raw provenance preserved for every fetched job so that the extraction decisions are fully auditable.
1. THE Jobs_Pipeline SHALL fetch the source payload and preserve it as a `RawDocument` in the `RawDocumentRepository`.
2. THE Jobs_Pipeline SHALL obtain the resulting `raw_document_id` and deterministically link it to the parsed `JobRecord`.
3. THE Job database model SHALL be updated to include a `raw_document_id` foreign key referencing `raw_documents.id`.
4. THE provenance linkage SHALL survive duplicate/idempotent processing (e.g., if the same job URL is fetched, it should link to the deduplicated RawDocument).
5. URL correlation SHALL NOT be used as a substitute for the explicit foreign key.
