# Requirements Document: Phase 6 - High-Fidelity AI News Ingestion

## Introduction

This specification defines requirements for a production-quality news ingestion vertical within the GraphOne Intelligence Pipeline. The system extends the existing async crawler infrastructure (Phases 1-5) to ingest, extract, deduplicate, and persist AI-related news articles from five distinct reputable sources.

The news vertical must integrate seamlessly with the existing architecture while enforcing strict anti-hallucination guarantees: the system MUST reject records with missing required data rather than inventing facts to satisfy schema validation.

**Critical Design Decisions (Requirements Hardening Review)**:

1. **Full-Text Extraction is Mandatory**: A news record is considered valid ONLY when full article text has been successfully extracted (minimum 100 characters). Records with only title/URL/date but no article body are rejected and counted separately as `extraction_failed`, NOT as `valid_records`. This ensures the canonical News dataset contains complete high-fidelity articles as required by the assessment.

2. **Strict 24-Hour Freshness with Clock Skew Handling**: Articles must be published within the last 24 hours. A configurable clock skew tolerance (default 60 seconds, max 300 seconds) handles minor server clock drift for "just published" articles. Articles with timestamps exceeding this tolerance are rejected as `future_dated`. This prevents false claims that future-scheduled articles satisfy the 24-hour guarantee.

3. **Five Verified AI News Sources**: The pipeline ingests from HackerNews (Algolia API), TechCrunch RSS, The Verge RSS, MIT Technology Review RSS, and Synced Review RSS. All source endpoints require verification against live network traffic during implementation before being considered production-ready.

4. **Complete Anti-Hallucination Compliance**: Missing title → reject. Missing URL → reject. Missing publication date → reject. Failed full-text extraction → reject. No LLMs may invent missing facts. No paywall/CAPTCHA/authentication bypass. Every canonical record retains legitimate source provenance.

5. **Observable Metrics**: Final statistics distinguish: `valid_records` (full-text extracted), `extraction_failed`, `fetch_failed`, `stale_records`, `future_dated_records`, `invalid_records`, `blocked_sources`, `duplicate_skipped`, and `persisted`.

## Glossary

- **News_Pipeline**: The orchestration component coordinating news source adapters, validation, freshness filtering, and persistence
- **Source_Adapter**: A concrete implementation of the SourceAdapter base class for a specific news source (RSS feed or API)
- **HTTP_Client**: The shared AsyncHttpClient instance providing connection pooling, retry/backoff, and bounded concurrency (src/crawlers/http.py)
- **Date_Engine**: The deterministic date extraction engine implementing the priority chain from Section 14 (src/extraction/dates.py)
- **Article_Extractor**: A component that extracts full article text from HTML article pages
- **Raw_Document_Repository**: The content-hash deduplicated provenance store (src/storage/repositories.py)
- **News_Repository**: The database repository for validated, deduplicated news records (unique on source_name + url)
- **Worker_Pool**: The bounded-concurrency async worker pool (src/pipeline/workers.py)
- **Freshness_Window**: A 24-hour time window measured from a reference time (defaults to pipeline start time)
- **Content_Hash**: A SHA-256 hex digest computed from raw article content for deduplication
- **Record_Type**: A string enum identifying the domain entity type (NEWS for this vertical)
- **Valid_Record**: A news record that has passed all validation gates: title exists, URL exists, source exists, publication timestamp exists and is fresh, AND full article text has been successfully extracted (minimum 100 characters)
- **Extraction_Failed**: A record where the article HTML was successfully fetched but full-text extraction returned None, empty string, or fewer than 100 characters; these records are preserved in Raw_Document_Repository for provenance but are NOT persisted to the News table
- **Clock_Skew_Tolerance**: A configurable time window (default 60 seconds, max 300 seconds) allowing timestamps slightly ahead of reference_time to account for server clock drift; articles exceeding this tolerance are rejected as future_dated
- **Future_Dated**: A record where published_at exceeds (reference_time + clock_skew_tolerance), indicating a genuinely future-scheduled article rather than minor server clock drift

## Requirements

### Requirement 1: News Source Discovery

**User Story:** As a pipeline operator, I want the system to discover AI news articles from five distinct reputable sources, so that I can ingest recent AI-related news at scale.

#### Acceptance Criteria

1. THE News_Pipeline SHALL register exactly five enabled news source adapters in the source registry (src/config/sources.py)
2. WHEN a source uses RSS/Atom discovery, THE Source_Adapter SHALL parse the feed using standard RSS 2.0/Atom XML schemas
3. WHEN a source uses JSON API discovery, THE Source_Adapter SHALL parse the JSON response according to the documented API schema
4. THE Source_Adapter SHALL yield DiscoveredUrl objects containing the article URL and any source-provided metadata (title, excerpt, publication date candidates)
5. THE News_Pipeline SHALL NOT invent or infer article URLs that are not explicitly provided by the source

### Requirement 2: Full Article Text Extraction

**User Story:** As a pipeline operator, I want full article text extracted from each article page, so that downstream analysis has complete article content rather than just titles or excerpts.

#### Acceptance Criteria

1. WHEN an article URL is discovered, THE Source_Adapter SHALL fetch the article page HTML using the shared HTTP_Client
2. THE Article_Extractor SHALL extract the main article content from HTML using a content extraction library (trafilatura or newspaper3k)
3. THE Article_Extractor SHALL return extracted text containing the article body, excluding navigation, ads, and boilerplate elements
4. IF article extraction fails or returns empty content, THE Source_Adapter SHALL log the failure, mark extracted_metadata["extraction_failed"] = true, preserve the raw HTML for provenance, and SHALL NOT persist the record to the News table
5. THE Source_Adapter SHALL NOT fabricate article text when extraction fails
6. THE Source_Adapter SHALL reject records where extracted text is None, empty string, or contains fewer than 100 characters
7. THE Article_Extractor SHALL preserve paragraph structure and whitespace in extracted text

### Requirement 3: Publication Date Extraction with Fallback Priority

**User Story:** As a pipeline operator, I want publication dates extracted using a deterministic priority chain, so that every article has a reliable publication timestamp without hallucination.

#### Acceptance Criteria

1. THE Source_Adapter SHALL attempt to extract publication dates using the Date_Engine priority chain in order: source-provided structured value → JSON-LD → OpenGraph/meta tags → time tag → visible text (absolute then relative)
2. WHEN a source provides a structured publication date (RSS pubDate, API timestamp), THE Source_Adapter SHALL pass it to Date_Engine as the structured_value parameter (highest priority)
3. WHEN structured_value extraction fails, THE Date_Engine SHALL parse the article HTML using JSON-LD, meta tags, and time tags
4. WHEN HTML parsing fails, THE Date_Engine SHALL parse visible date text in the article body for absolute dates, then relative date expressions ("2 hours ago", "yesterday")
5. THE Date_Engine SHALL normalize all extracted dates to UTC using the to_utc() function
6. IF no date extraction strategy succeeds, THE Source_Adapter SHALL set published_at to None and reject the record during validation
7. THE Source_Adapter SHALL pass the pipeline reference_time to parse_relative_date() for deterministic relative date resolution

### Requirement 4: Strict 24-Hour Freshness Filtering

**User Story:** As a pipeline operator, I want only articles published within the last 24 hours included in the final dataset, so that the news vertical delivers fresh, timely content.

#### Acceptance Criteria

1. THE News_Pipeline SHALL capture a reference_time at pipeline start using datetime.now(timezone.utc)
2. WHEN a parsed record has a published_at timestamp, THE News_Pipeline SHALL call is_fresh(published_at, reference_time, window_hours=24, clock_skew_tolerance_seconds=CLOCK_SKEW_TOLERANCE_SECONDS)
3. THE is_fresh() function SHALL return True only when (reference_time - timedelta(hours=window_hours)) <= published_at <= (reference_time + timedelta(seconds=clock_skew_tolerance_seconds))
4. THE is_fresh() function SHALL tolerate minor clock skew by accepting timestamps within a small tolerance (configurable via CLOCK_SKEW_TOLERANCE_SECONDS, default 60 seconds) ahead of reference_time, representing "just published" server clock drift
5. WHEN published_at exceeds (reference_time + clock_skew_tolerance), THE News_Pipeline SHALL reject the record as future-dated and increment a future_dated_records counter
6. THE clock skew tolerance SHALL be documented as handling server clock drift, not accepting genuinely future-scheduled articles
7. IF a record's published_at is before the freshness window (stale), THE News_Pipeline SHALL reject the record and increment a stale_records counter
8. THE News_Pipeline SHALL log rejected stale records with published_at, reference_time, and age_hours for observability
9. THE News_Pipeline SHALL log future_date_rejected for any record where published_at > (reference_time + clock_skew_tolerance) with published_at, reference_time, and delta_seconds
10. THE CLOCK_SKEW_TOLERANCE_SECONDS environment variable SHALL default to 60 seconds and SHALL NOT exceed 300 seconds (5 minutes)
11. THE Freshness_Window SHALL default to 24 hours and MAY be overridden via FRESHNESS_WINDOW_HOURS environment variable

### Requirement 5: URL-Based Deduplication

**User Story:** As a pipeline operator, I want articles deduplicated by source_name + url at the database level, so that the same article from the same source is stored exactly once regardless of concurrent workers or repeated runs.

#### Acceptance Criteria

1. THE News table SHALL enforce a unique constraint on (source_name, url) named uq_news_source_url
2. THE News_Repository SHALL use INSERT ... ON CONFLICT DO NOTHING to idempotently persist news records
3. WHEN two concurrent workers attempt to persist the same (source_name, url), THE database SHALL accept exactly one row
4. THE News_Pipeline SHALL normalize article URLs using normalize_url() before persistence
5. THE News_Pipeline SHALL NOT deduplicate across different sources (same article URL from different sources → separate rows)

### Requirement 6: Content-Hash Deduplication for Raw Provenance

**User Story:** As a pipeline operator, I want raw article HTML deduplicated by content hash in the Raw_Document_Repository, so that identical content fetched from different URLs or runs is stored once.

#### Acceptance Criteria

1. THE Raw_Document_Repository SHALL compute a Content_Hash using SHA-256 on the raw HTML bytes
2. THE Raw_Documents table SHALL enforce a unique constraint on content_hash named uq_raw_documents_content_hash
3. THE Raw_Document_Repository SHALL use INSERT ... ON CONFLICT (content_hash) DO UPDATE SET retrieved_at = EXCLUDED.retrieved_at
4. WHEN identical HTML is fetched from different URLs, THE Raw_Document_Repository SHALL store one raw_document and update retrieved_at
5. THE News_Repository SHALL link each news record to its raw_document_id for provenance traceability

### Requirement 7: Raw Content Provenance Preservation

**User Story:** As a pipeline operator, I want raw HTML and publication date extraction metadata preserved for every fetched article, so that extraction decisions are auditable and recoverable.

#### Acceptance Criteria

1. THE Source_Adapter SHALL persist raw article HTML to the Raw_Document_Repository before validation
2. THE Raw_Document_Repository SHALL store source_name, source_url, canonical_url, retrieved_at, http_status, content_hash, and publication_date_candidates
3. THE publication_date_candidates field SHALL be a JSONB object containing all attempted extraction strategies and their results (e.g., {"rss_pubDate": "2024-01-15T10:30:00Z", "json_ld": null, "meta_tag": "2024-01-15T10:30:00Z"})
4. THE Raw_Document_Repository SHALL set raw_content_location to null initially (raw HTML storage to S3/MinIO is deferred to Phase 9+)
5. THE News_Repository SHALL populate raw_document_id with the corresponding raw_documents.id for linkage

### Requirement 8: Retry, Rate Limit, and Failure Handling

**User Story:** As a pipeline operator, I want the crawler to handle transient failures, rate limits, and permanent blocks gracefully, so that temporary issues do not cause data loss and blocked sources do not hang the pipeline.

#### Acceptance Criteria

1. THE Source_Adapter SHALL use the shared HTTP_Client for all network requests to enforce retry/backoff logic
2. WHEN the HTTP_Client encounters a 429 response, THE retry_async function SHALL honor Retry-After header and retry with exponential backoff
3. WHEN the HTTP_Client encounters a 403 with X-RateLimit-Remaining: 0, THE HTTP_Client SHALL raise RateLimitError (retryable)
4. WHEN the HTTP_Client encounters a 403 without rate limit headers, THE HTTP_Client SHALL raise BlockedSourceError (not retried)
5. WHEN the HTTP_Client encounters a 401 response, THE HTTP_Client SHALL raise BlockedSourceError (not retried, not bypassed)
6. WHEN a BlockedSourceError occurs, THE Worker_Pool SHALL log the blocked source, increment stats.blocked, and continue processing other URLs
7. WHEN a fetch or parse operation fails after max_retries, THE Worker_Pool SHALL log the error, increment stats.fetch_failed, and continue processing
8. THE News_Pipeline SHALL NOT fabricate success when a source is blocked or fails

### Requirement 9: Schema Validation Gate

**User Story:** As a pipeline operator, I want every parsed news record validated against a strict Pydantic schema before persistence, so that invalid or incomplete records are rejected rather than stored with fabricated fields.

#### Acceptance Criteria

1. THE News_Pipeline SHALL define a NewsRecord Pydantic schema with required fields: title (min_length=1), url (HttpUrl shape), source_name (min_length=1), published_at (datetime, UTC, not nullable), full_text_location (non-null, min_length=1)
2. THE NewsRecord schema SHALL define optional fields: extracted_metadata (dict), raw_document_id (UUID, nullable)
3. THE News_Pipeline SHALL call a validate_news_record(data: dict) function returning (NewsRecord | None, error_message | None)
4. WHEN validation succeeds, THE News_Pipeline SHALL persist the validated record to the News_Repository
5. WHEN validation fails, THE News_Pipeline SHALL log the validation error with the source_url and error message, increment an invalid_records counter, and NOT persist the record
6. THE NewsRecord schema SHALL NOT provide default values for required fields (title, url, source_name, published_at, full_text_location must be supplied by the adapter)

### Requirement 10: Bounded Concurrency and Worker Pool Integration

**User Story:** As a pipeline operator, I want news adapters to run with bounded concurrency via the shared Worker_Pool, so that the system respects MAX_CONCURRENCY and does not overwhelm sources or the database.

#### Acceptance Criteria

1. THE News_Pipeline SHALL instantiate each Source_Adapter with the shared HTTP_Client (which enforces concurrency via asyncio.Semaphore)
2. THE News_Pipeline SHALL call run_adapter(adapter, max_concurrency=MAX_CONCURRENCY, max_items=per_adapter_target) for each news source
3. THE Worker_Pool SHALL enforce that at most max_concurrency fetch/parse operations run concurrently per adapter
4. THE Worker_Pool SHALL deduplicate URLs within a single run using normalize_url() and a seen_urls set
5. THE Worker_Pool SHALL return RunStats containing discovered, fetched, fetch_failed, parsed_records, skipped_duplicate, blocked, and errors counts
6. THE News_Pipeline SHALL aggregate RunStats from all adapters and log the totals

### Requirement 11: Never Fabricate Data

**User Story:** As a pipeline operator, I want the system to explicitly reject records with missing required data, so that the assessment evaluation does not penalize fabricated or hallucinated content.

#### Acceptance Criteria

1. THE Source_Adapter SHALL NOT invent article URLs, titles, publication dates, or article text that are not present in the source response
2. WHEN a required field (title, url, published_at, full_text) cannot be extracted, THE Source_Adapter SHALL set it to None or raise a validation error
3. THE News_Pipeline SHALL reject records where published_at is None during validation
4. THE News_Pipeline SHALL reject records where full_text_location is None during validation
5. THE News_Pipeline SHALL NOT use LLMs or heuristics to guess missing publication dates
6. WHEN article text extraction fails, THE Source_Adapter SHALL set extracted_metadata["extraction_failed"] = true, preserve raw HTML in Raw_Document_Repository, and SHALL reject the record (extraction failure prevents persistence to News table)
7. THE News_Pipeline SHALL log all rejected records with rejection_reason for audit

### Requirement 12: News Source Adapter Implementations

**User Story:** As a pipeline operator, I want concrete news source adapters for five reputable AI news sources, so that the pipeline can ingest diverse, high-quality AI news content.

#### Acceptance Criteria

1. THE News_Pipeline SHALL implement a HackerNewsAIAdapter using the Algolia HN API with endpoint hn.algolia.com/api/v1/search_by_date with parameters: tags=story, query="artificial intelligence" OR "machine learning" OR "deep learning" OR "neural network" OR "LLM" OR "GPT", sorted by date, with created_at as the publication date
2. THE HackerNewsAIAdapter SHALL fetch the linked article URL from each HN story and extract full text from the destination article, not the HN comments page
3. THE HackerNewsAIAdapter SHALL use the HN story's created_at timestamp as the publication date (not the destination article's date)
4. THE News_Pipeline SHALL implement a TechCrunchAIAdapter parsing the RSS feed at techcrunch.com/category/artificial-intelligence/feed/ with pubDate extraction
5. THE News_Pipeline SHALL implement a TheVergeAIAdapter parsing the RSS feed at www.theverge.com/rss/ai-artificial-intelligence/index.xml with pubDate extraction
6. THE News_Pipeline SHALL implement a MITTechReviewAIAdapter parsing the RSS feed at technologyreview.com/topic/artificial-intelligence/feed with pubDate extraction (endpoint verification required during implementation)
7. THE News_Pipeline SHALL implement a SyncedReviewAdapter parsing the RSS feed at syncedreview.com/feed/ with pubDate extraction (lower publication frequency expected)
8. WHEN an adapter uses RSS, THE Source_Adapter SHALL parse XML using feedparser or xml.etree.ElementTree
9. WHEN an adapter uses JSON API, THE Source_Adapter SHALL parse JSON using the json module
10. THE Source_Adapter SHALL set source_name to match the registered name in SOURCE_REGISTRY (e.g., "hackernews_ai", "techcrunch_ai_rss", "theverge_ai_rss")
11. BEFORE live ingestion is considered successful, THE implementation SHALL verify each source endpoint against real network traffic in a deployment environment with normal internet egress

### Requirement 13: CLI Integration

**User Story:** As a pipeline operator, I want to run the news pipeline via `python -m src.main --vertical news`, so that it integrates with the existing CLI and behaves consistently with the research vertical.

#### Acceptance Criteria

1. THE main.py CLI SHALL accept `--vertical news` as a valid vertical argument
2. WHEN `--vertical news` is specified, THE CLI SHALL call run_news_pipeline(target=args.target, max_concurrency=args.workers, reference_time=pipeline_start)
3. THE CLI SHALL log pipeline start, end, duration, and aggregate stats (total discovered, fetched, full_text_extracted, extraction_failed, rejected stale, rejected future_dated, rejected invalid, persisted)
4. THE CLI SHALL accept `--target N` to limit the number of articles discovered per source (evenly split across adapters)
5. THE CLI SHALL accept `--workers N` to set max_concurrency (defaults to MAX_CONCURRENCY from settings)
6. THE CLI SHALL create all database tables via Base.metadata.create_all() before running the pipeline
7. THE CLI SHALL report final counts: valid_records, full_text_extracted, extraction_failed, stale_records, future_dated_records, invalid_records, fetch_failed, blocked_sources
8. THE CLI SHALL NOT count extraction_failed records toward valid_records total

### Requirement 14: Database Integration

**User Story:** As a pipeline operator, I want news records persisted to the existing news table with proper foreign key linkage and transaction safety, so that news data integrates with the existing schema.

#### Acceptance Criteria

1. THE News_Pipeline SHALL use the existing News SQLAlchemy model from src/storage/models.py (no schema changes required)
2. THE News_Repository SHALL provide an async upsert_news(record: NewsRecord, raw_document_id: uuid.UUID | None) method
3. THE News_Repository upsert_news method SHALL execute INSERT INTO news ... ON CONFLICT (source_name, url) DO NOTHING
4. THE News_Repository SHALL commit each batch of records within a single async transaction
5. WHEN a database constraint violation occurs (duplicate key), THE News_Repository SHALL NOT raise an error (idempotent upsert)
6. THE News_Pipeline SHALL populate collected_at with the pipeline reference_time (not per-record fetch time)

### Requirement 15: Logging and Observability

**User Story:** As a pipeline operator, I want structured logging for every stage of the news pipeline, so that I can diagnose failures, measure throughput, and audit extraction decisions.

#### Acceptance Criteria

1. THE News_Pipeline SHALL log pipeline_start with vertical="news", reference_time, target, max_concurrency, and clock_skew_tolerance_seconds
2. THE Source_Adapter SHALL log fetch_complete for every successful article page fetch with url, status, content_type, and bytes
3. THE Source_Adapter SHALL log fetch_failed for every failed fetch with url, error, and error_type
4. THE Source_Adapter SHALL log extraction_failed when article text extraction fails with url, extraction_library, bytes_fetched, and reason
5. THE News_Pipeline SHALL log validation_failed for every rejected record with source_url, error, and validation_error
6. THE News_Pipeline SHALL log freshness_rejected for every stale record with source_url, published_at, age_hours, and freshness_window_hours
7. THE News_Pipeline SHALL log future_date_rejected for every future-dated record with source_url, published_at, reference_time, and delta_seconds
8. THE News_Pipeline SHALL log extraction_failed for every failed full-text extraction with url, bytes_fetched, extraction_library, and reason
9. THE News_Pipeline SHALL distinguish in final stats between: valid_records (full-text extracted), extraction_failed (no body text), fetch_failed (network failure), stale_records (>24h), future_dated_records (clock skew exceeded), invalid_records (schema failure), blocked (source blocked), persisted (deduplicated accepted)
10. THE News_Pipeline SHALL NOT count extraction_failed records toward valid_records total
11. THE News_Pipeline SHALL log pipeline_complete with aggregate stats: discovered, fetched, full_text_extracted, extraction_failed, validated, rejected_stale, rejected_future_dated, rejected_invalid, persisted, fetch_failed, blocked, duplicate_skipped, duration_seconds

### Requirement 16: Testing Strategy with Mocked Fixtures

**User Story:** As a pipeline operator, I want comprehensive tests for the news pipeline using mocked RSS/HTML fixtures, so that the default test suite never depends on live network access.

#### Acceptance Criteria

1. THE test suite SHALL provide realistic RSS 2.0 XML fixtures for each RSS-based adapter in tests/fixtures/ (e.g., techcrunch_ai_sample.xml)
2. THE test suite SHALL provide realistic JSON fixtures for each JSON API adapter in tests/fixtures/ (e.g., hackernews_ai_sample.json)
3. THE test suite SHALL provide realistic article HTML fixtures for article extraction tests in tests/fixtures/ (e.g., sample_article.html)
4. THE test suite SHALL use respx or httpx.MockTransport to mock HTTP responses for adapter tests
5. THE test suite SHALL test date extraction priority chain with fixtures containing JSON-LD, meta tags, time tags, and visible date text
6. THE test suite SHALL test freshness filtering with frozen reference_time and articles at 23h, 24h, 25h age
7. THE test suite SHALL test URL deduplication with concurrent workers racing on the same article URL (similar to test_concurrent_workers_racing_same_url_produce_one_row)
8. THE test suite SHALL test validation rejection for missing required fields (title, url, published_at)
9. THE test suite SHALL test BlockedSourceError handling with mocked 403/401 responses
10. THE test suite SHALL test RateLimitError handling with mocked 429 + Retry-After responses
11. THE test suite SHALL include at least 40 new tests covering adapters (5 sources × 4 tests each), validation (5 tests), freshness (5 tests), deduplication (3 tests), pipeline integration (3 tests)

### Requirement 17: Full-Text Storage Strategy

**User Story:** As a pipeline operator, I want extracted article text stored in a way that supports both immediate database storage and future migration to external blob storage, so that the system is scalable.

#### Acceptance Criteria

1. THE News table full_text_location field SHALL store a string path/URI identifying where the full text is stored
2. FOR Phase 6, THE Source_Adapter SHALL store extracted text directly in the News table extracted_metadata["full_text"] field as JSONB (database storage)
3. THE News_Repository SHALL set full_text_location to "inline:extracted_metadata.full_text" when text is stored in extracted_metadata
4. THE Article_Extractor SHALL truncate extracted text to 100,000 characters maximum to avoid excessive database column size
5. WHEN extracted_metadata["full_text"] exceeds 100KB, THE Source_Adapter SHALL log a warning and truncate with extracted_metadata["truncated"] = true
6. THE News_Pipeline design SHALL support future migration to S3/MinIO by changing full_text_location to "s3://bucket/key" without schema changes (deferred to Phase 9+)

### Requirement 18: Network Sandboxing and Deployment Considerations

**User Story:** As a pipeline operator, I want the news adapters tested with mocked fixtures in the sandbox and verified against live sources after deployment, so that network restrictions do not block development.

#### Acceptance Criteria

1. THE test suite SHALL use mocked fixtures for all news source HTTP requests (no live network dependencies)
2. THE README SHALL document that news sources are unreachable from the sandboxed development environment (egress restricted to pypi.org, npmjs.org, github.com)
3. THE README SHALL document that news adapters will be verified against live sources after deployment to an environment with normal internet egress
4. THE Source_Adapter SHALL NOT include network availability checks or fallback logic for sandbox restrictions (deployment environment is responsible for network access)
5. THE SOURCE_REGISTRY documentation SHALL note which sources have been verified against live traffic (initially none in sandbox, updated post-deployment)

### Requirement 19: Anti-Hallucination Compliance

**User Story:** As a pipeline operator, I want the news pipeline to explicitly comply with the assessment's anti-hallucination requirements, so that the evaluation does not disqualify fabricated data.

#### Acceptance Criteria

1. THE News_Pipeline SHALL NOT use LLMs to invent publication dates, article titles, or article URLs
2. THE News_Pipeline SHALL NOT infer missing facts to satisfy schema validation (e.g., defaulting published_at to pipeline start time)
3. WHEN required source data (title, url, published_at) is unavailable, THE News_Pipeline SHALL reject the record and log the rejection
4. THE News_Pipeline SHALL NOT bypass paywalls, CAPTCHA, or authentication to retrieve article text
5. THE News_Pipeline SHALL log every rejected record with rejection_reason in ProcessingError table for audit
6. THE News_Pipeline documentation SHALL explicitly state the anti-hallucination guarantee in the docstring and README

### Requirement 20: Performance and Scalability

**User Story:** As a pipeline operator, I want the news pipeline to scale to hundreds of concurrent workers and thousands of articles per run, so that it meets production throughput requirements.

#### Acceptance Criteria

1. THE News_Pipeline SHALL support max_concurrency up to 200 workers without deadlock or resource exhaustion
2. THE HTTP_Client connection pool SHALL be sized at max_concurrency × 2 to support keep-alive connections
3. THE Worker_Pool SHALL use asyncio.Semaphore to enforce concurrency limits rather than spawning unbounded tasks
4. THE News_Repository SHALL batch persist records in groups of 100 to reduce transaction overhead
5. THE News_Pipeline SHALL process 1000 articles in under 5 minutes at max_concurrency=50 (measured post-deployment, not in sandbox)
6. THE News_Pipeline SHALL log throughput metrics: articles_per_second, average_fetch_latency_ms, average_parse_latency_ms

### Requirement 21: Full-Text Extraction Mandatory for Valid Records

**User Story:** As a pipeline operator, I want only articles with successfully extracted full-text body content to be persisted as valid news records, so that the canonical dataset contains complete high-fidelity articles.

#### Acceptance Criteria

1. THE NewsRecord Pydantic schema SHALL require full_text_location to be non-null for validation to pass
2. THE Article_Extractor SHALL return non-empty extracted text containing at least 100 characters for a successful extraction
3. WHEN article text extraction returns None or empty string or fewer than 100 characters, THE Source_Adapter SHALL set extraction status to failed and SHALL NOT persist the record to the News table
4. THE News_Pipeline SHALL count extraction failures separately as extraction_failed, distinct from fetch_failed
5. THE News_Pipeline SHALL log extraction_failed records with url, extraction_library, http_status, bytes_fetched, and reason
6. THE News_Pipeline final stats SHALL report: full_text_extracted (success), extraction_failed (failure), and SHALL NOT count extraction_failed toward valid_records
7. THE extracted_metadata["extraction_failed"] flag SHALL prevent record persistence to the News table
8. THE Raw_Document_Repository SHALL still persist raw HTML even when extraction fails (provenance only, not a valid canonical record)
