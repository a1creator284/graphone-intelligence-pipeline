# Implementation Plan: Phase 6 - High-Fidelity AI News Ingestion

## Overview

This implementation extends the GraphOne Intelligence Pipeline with a production-quality news ingestion vertical. The system ingests AI-related news articles from five distinct sources (HackerNews, TechCrunch, The Verge, MIT Tech Review, Synced Review) with strict 24-hour freshness guarantees, full-text extraction, and complete anti-hallucination compliance.

**Key Implementation Principles**:
- Reuse existing infrastructure (HTTP_Client, Date_Engine, Worker_Pool, Raw_Document_Repository)
- Explicit rejection over fabrication (missing data → rejection, never invention)
- Database-level deduplication via unique constraints
- Observable failure modes with distinct counters for each rejection category
- Testability without network dependencies (all mocked fixtures)

## Tasks

- [ ] 1. Set up news vertical infrastructure and base components
  - [ ] 1.1 Create `src/pipeline/news.py` with `run_news_pipeline()` function matching research pipeline pattern
  - [ ] 1.2 Add validation schema `NewsRecord` to `src/validation/schemas.py` with required fields (title, url, source_name, published_at, full_text_location)
  - [ ] 1.3 Create `NewsRepository` in `src/storage/repositories.py` with upsert method using ON CONFLICT DO NOTHING
  - [ ] 1.4 Update `src/main.py` CLI to handle `--vertical news` argument
  - [ ] 1.5 Add Vertical.NEWS to `src/config/sources.py` enum
  - _Requirements: 13.1, 13.2, 14.1, 14.2_

- [ ] 2. Implement article text extraction component
  - [ ] 2.1 Create `src/extraction/articles.py` with `ArticleExtractor` class
    - Implement `extract_text(html: str, source_url: str) -> str | None` using trafilatura as primary extraction library
    - Add fallback to newspaper3k when trafilatura returns None or empty string
    - Enforce minimum content length of 100 characters (reject shorter extractions)
    - Enforce maximum content length of 100,000 characters (truncate with warning)
    - Return None when both libraries fail or content is below minimum length
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 17.4, 17.5_

  - [ ] 2.2 Write unit tests for article extraction (MANDATORY: validates assessment full-text extraction criteria)
    - Test successful extraction with trafilatura using mocked HTML fixture
    - Test fallback to newspaper3k when trafilatura fails
    - Test rejection of content below 100 characters
    - Test truncation of content exceeding 100KB with truncated flag
    - Test handling of malformed HTML gracefully
    - _Requirements: 2.3, 2.4, 16.1, 16.3_

- [ ] 3. Implement freshness validation with clock skew tolerance
  - [ ] 3.1 Create `src/validation/freshness.py` with `is_fresh()` function
    - Accept parameters: published_at, reference_time, window_hours (default 24), clock_skew_tolerance_seconds (default 60)
    - Return tuple of (is_fresh: bool, rejection_reason: str | None)
    - Accept timestamps within window: (reference_time - window_hours) <= published_at <= (reference_time + clock_skew_tolerance)
    - Reject stale records where published_at < (reference_time - window_hours)
    - Reject future-dated records where published_at > (reference_time + clock_skew_tolerance)
    - Add CLOCK_SKEW_TOLERANCE_SECONDS and FRESHNESS_WINDOW_HOURS to `src/config/settings.py`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.10, 4.11_

  - [ ] 3.2 Write unit tests for freshness validation (MANDATORY: validates assessment 24-hour freshness criteria)
    - Test acceptance of articles within 24-hour window with frozen reference_time
    - Test rejection of stale articles beyond 24 hours
    - Test acceptance of articles within clock skew tolerance (e.g., 30 seconds ahead)
    - Test rejection of future-dated articles exceeding tolerance (e.g., 5 minutes ahead)
    - Test configurable window_hours and clock_skew_tolerance parameters
    - _Requirements: 4.1, 4.3, 4.4, 4.5, 16.6_

- [ ] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Create base RSS news adapter class
  - [ ] 5.1 Create `src/crawlers/news_base.py` with `RSSNewsAdapter` abstract class
    - Inherit from `SourceAdapter` base class
    - Implement `discover()` method to fetch and parse RSS feed XML using feedparser
    - Extract title, link, pubDate, description from each RSS item
    - Yield `DiscoveredUrl` with url=link and metadata containing RSS fields
    - Implement `fetch()` method to fetch article page HTML using shared HTTP_Client
    - Implement `parse()` method to extract full text using ArticleExtractor
    - Use RSS pubDate as structured_value parameter for Date_Engine (highest priority)
    - Construct `ParsedRecord` with record_type="NEWS" when extraction succeeds
    - Set extracted_metadata["extraction_failed"] = True and return empty list when extraction fails
    - Store extracted text using deterministic content-addressable location: compute SHA-256 hash of extracted text, set full_text_location="local:sha256:{hash}", store in deterministic location for demo (migration-ready for S3/MinIO)
    - _Requirements: 12.8, 3.1, 3.2, 2.1, 2.4, 2.6, 11.6, 17.2, 17.3_

  - [ ] 5.2 Write unit tests for RSS adapter base class (MANDATORY: validates assessment date extraction criteria)
    - Test RSS feed parsing with mocked XML fixture containing multiple articles
    - Test article URL discovery and metadata extraction from RSS items
    - Test date extraction priority: RSS pubDate as structured_value (highest priority)
    - Test rejection when full-text extraction fails (returns empty ParsedRecord list)
    - _Requirements: 16.1, 16.2, 16.5, 16.8_

- [ ] 6. Implement HackerNews Algolia API adapter
  - [ ] 6.1 Create `src/crawlers/hackernews.py` with `HackerNewsAIAdapter` class
    - Inherit from `SourceAdapter` base class
    - Set BASE_URL to "https://hn.algolia.com/api/v1/search_by_date"
    - Configure query params: tags=story, query with AI-related terms (artificial intelligence, machine learning, deep learning, neural network, LLM, GPT)
    - Implement `discover()` to fetch JSON API response and parse hits array
    - Extract objectID, title, url (article destination), created_at from each hit
    - Yield `DiscoveredUrl` with article URL and HN metadata
    - Implement `fetch()` to fetch destination article page (not HN comments)
    - Implement `parse()` to extract full text and use HN created_at as structured_value for date
    - Set source_name to "hackernews_ai"
    - _Requirements: 12.1, 12.2, 12.3, 12.9, 12.10, 1.3, 1.4_

  - [ ] 6.2 Write unit tests for HackerNews adapter (MANDATORY: validates assessment source adapter criteria)
    - Test Algolia API response parsing with mocked JSON fixture
    - Test AI-related query parameter construction
    - Test article URL extraction (not HN comments URL)
    - Test date extraction using HN created_at as structured_value
    - _Requirements: 16.2, 16.5, 16.9_

- [ ] 7. Implement RSS-based news adapters for four sources
  - [ ] 7.1 Create `src/crawlers/techcrunch.py` with `TechCrunchAIAdapter` class
    - Inherit from `RSSNewsAdapter` base class
    - Set feed_url to "https://techcrunch.com/category/artificial-intelligence/feed/"
    - Set source_name to "techcrunch_ai_rss"
    - _Requirements: 12.4, 12.10_

  - [ ] 7.2 Create `src/crawlers/theverge.py` with `TheVergeAIAdapter` class
    - Inherit from `RSSNewsAdapter` base class
    - Set feed_url to "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"
    - Set source_name to "theverge_ai_rss"
    - _Requirements: 12.5, 12.10_

  - [ ] 7.3 Create `src/crawlers/mitreview.py` with `MITTechReviewAIAdapter` class
    - Inherit from `RSSNewsAdapter` base class
    - Set feed_url to "https://www.technologyreview.com/topic/artificial-intelligence/feed"
    - Set source_name to "mit_technology_review_ai_rss"
    - Add note that endpoint requires verification during deployment (sandbox has restricted egress)
    - _Requirements: 12.6, 12.10, 18.2, 18.3_

  - [ ] 7.4 Create `src/crawlers/synced.py` with `SyncedReviewAdapter` class
    - Inherit from `RSSNewsAdapter` base class
    - Set feed_url to "https://syncedreview.com/feed/"
    - Set source_name to "synced_review_rss"
    - _Requirements: 12.7, 12.10_

  - [ ] 7.5 Write unit tests for all four RSS adapters (MANDATORY: validates assessment source adapter criteria)
    - Test TechCrunch adapter with mocked RSS XML fixture (tests/fixtures/techcrunch_ai_sample.xml)
    - Test The Verge adapter with mocked RSS XML fixture (tests/fixtures/theverge_ai_sample.xml)
    - Test MIT Tech Review adapter with mocked RSS XML fixture (tests/fixtures/mitreview_ai_sample.xml)
    - Test Synced Review adapter with mocked RSS XML fixture (tests/fixtures/synced_sample.xml)
    - Each test verifies: feed parsing, URL discovery, pubDate extraction, full-text extraction
    - _Requirements: 16.1, 16.2_

- [ ] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Implement news pipeline orchestration
  - [ ] 9.1 Wire news adapters in news pipeline
    - Import all five adapter classes (HackerNewsAIAdapter, TechCrunchAIAdapter, TheVergeAIAdapter, MITTechReviewAIAdapter, SyncedReviewAdapter)
    - Create ADAPTER_CLASSES list with all five adapters
    - Instantiate each adapter with shared HTTP_Client
    - Split target evenly across adapters using per_adapter_target = max(1, target // len(ADAPTER_CLASSES))
    - Call run_adapter() for each adapter with max_concurrency parameter
    - Aggregate RunStats across all five adapters
    - _Requirements: 1.1, 10.2, 10.6, 13.4_

  - [ ] 9.2 Implement validation and persistence flow in news pipeline
    - Capture reference_time at pipeline start using datetime.now(timezone.utc)
    - For each ParsedRecord: call validate_news_record() to create NewsRecord schema
    - For validated records: call is_fresh() to check 24-hour freshness window
    - Persist raw HTML to Raw_Document_Repository with extraction_status and publication_date_candidates
    - Persist validated records to News_Repository using upsert method
    - Track distinct counters: valid_records, extraction_failed, stale_records, future_dated_records, invalid_records, fetch_failed, blocked, duplicate_skipped, persisted
    - Set collected_at to reference_time (not per-record fetch time)
    - _Requirements: 4.1, 4.2, 9.1, 9.2, 9.3, 9.4, 9.5, 7.1, 7.2, 7.5, 14.6, 15.9, 15.10, 15.11_

  - [ ] 9.3 Add structured logging to news pipeline
    - Log pipeline_start with vertical, reference_time, target, max_concurrency, clock_skew_tolerance_seconds
    - Log fetch_complete for successful fetches with url, status, content_type, bytes
    - Log fetch_failed for network failures with url, error, error_type
    - Log extraction_failed when article text extraction fails with url, bytes_fetched, extraction_library, reason
    - Log validation_failed for schema validation errors with url, error
    - Log freshness_rejected for stale records with url, published_at, age_hours
    - Log future_date_rejected for future-dated records with url, published_at, delta_seconds
    - Log pipeline_complete with aggregate stats: discovered, fetched, full_text_extracted, extraction_failed, validated, rejected_stale, rejected_future_dated, rejected_invalid, persisted, fetch_failed, blocked, duplicate_skipped, duration_seconds
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 15.8, 15.9, 15.11_

  - [ ] 9.4 Write integration tests for news pipeline (MANDATORY: validates assessment pipeline resilience criteria)
    - Test end-to-end pipeline with all five mocked adapters
    - Test stats aggregation across multiple adapters (discovered, fetched, persisted)
    - Test mixed success and failure handling (some succeed, some fail, pipeline continues)
    - _Requirements: 16.1, 16.11_

- [ ] 10. Implement error handling (NO independent retry loops - all retry logic stays in shared HTTP_Client)
  - [ ] 10.1 Add BlockedSourceError and RateLimitError classification to news adapters
    - Catch BlockedSourceError (403 without rate limit headers, 401) and log blocked source
    - Increment stats.blocked counter and continue processing other URLs
    - Catch RateLimitError (429, 403 with X-RateLimit-Remaining: 0) - shared HTTP_Client handles retry automatically
    - Catch PayloadTooLargeError (413) and log oversized response
    - DO NOT implement independent retry loops - all retry/backoff/jitter logic remains centralized in AsyncHttpClient
    - Adapters classify terminal errors and update pipeline statistics/logging only
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8_

  - [ ] 10.2 Write unit tests for error handling (MANDATORY: validates assessment 429/413/403 behavior criteria)
    - Test BlockedSourceError (403) logs blocked source and continues
    - Test RateLimitError (429) with Retry-After header - verify HTTP_Client handles retry (adapter does not retry independently)
    - Test 401 Unauthorized triggers BlockedSourceError (not retried)
    - Test PayloadTooLargeError (413) logs error and continues
    - Test fetch failures after max_retries log error and continue
    - _Requirements: 16.9, 16.10_

- [ ] 11. Create test fixtures for all news sources
  - [ ] 11.1 Create RSS XML fixtures for four RSS-based sources
    - Create tests/fixtures/techcrunch_ai_sample.xml with realistic TechCrunch RSS 2.0 XML structure
    - Create tests/fixtures/theverge_ai_sample.xml with realistic The Verge RSS 2.0 XML structure
    - Create tests/fixtures/mitreview_ai_sample.xml with realistic MIT Tech Review RSS 2.0 XML structure
    - Create tests/fixtures/synced_sample.xml with realistic Synced Review RSS 2.0 XML structure
    - Each fixture should contain 3-5 article items with title, link, pubDate, description
    - _Requirements: 16.1, 16.2_

  - [ ] 11.2 Create JSON fixture for HackerNews Algolia API
    - Create tests/fixtures/hackernews_ai_sample.json with realistic Algolia API response structure
    - Include hits array with 3-5 story objects containing objectID, title, url, created_at
    - Include AI-related article titles and URLs
    - _Requirements: 16.2_

  - [ ] 11.3 Create HTML article fixtures for extraction testing
    - Create tests/fixtures/sample_article_techcrunch.html with realistic article HTML structure
    - Create tests/fixtures/sample_article_verge.html with realistic article HTML structure
    - Create tests/fixtures/sample_article_mit.html with realistic article HTML structure
    - Each fixture should include JSON-LD datePublished, meta tags, time tags, and visible date text
    - Each fixture should contain main article content (>100 characters) for extraction testing
    - _Requirements: 16.3, 16.5_

- [ ] 12. Implement deduplication tests
  - [ ] 12.1 Write URL deduplication tests (MANDATORY: validates assessment URL deduplication criteria)
    - Test concurrent workers racing on same (source_name, url) produce exactly one row in News table
    - Test same article URL from different sources produces separate rows (per-source dedup)
    - Test URL normalization before persistence using normalize_url()
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 16.7_

  - [ ] 12.2 Write content-hash deduplication tests (MANDATORY: validates assessment content-hash deduplication criteria)
    - Test identical HTML from different URLs produces one raw_document row
    - Test content_hash uniqueness constraint enforcement
    - Test race condition handling: concurrent inserts return winner's row
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 16.7_

- [ ] 13. Register news adapters in source configuration
  - [ ] 13.1 Update src/config/sources.py to add all five news source adapters to SOURCE_REGISTRY
    - Register hackernews_ai, techcrunch_ai_rss, theverge_ai_rss, mit_technology_review_ai_rss, synced_review_rss
    - Set enabled=True for all five adapters
    - Set vertical=Vertical.NEWS for all five adapters
    - Add documentation note that endpoints require verification post-deployment (sandbox egress restricted)
    - _Requirements: 1.1, 12.10, 18.2, 18.3, 18.4_

- [ ] 14. Wire news vertical into CLI and test end-to-end
  - [ ] 14.1 Complete CLI integration for news vertical
    - Add _run_news() function to src/main.py matching _run_research() pattern
    - Call run_news_pipeline() with target, max_concurrency, reference_time parameters
    - Log aggregate stats: discovered, fetched, full_text_extracted, extraction_failed, rejected, persisted
    - Create database tables via Base.metadata.create_all() before pipeline execution
    - _Requirements: 13.1, 13.2, 13.3, 13.6, 13.7, 13.8, 14.6_

  - [ ] 14.2 Write CLI integration test (MANDATORY: validates assessment CLI integration criteria)
    - Test `python -m src.main --vertical news --target 100 --workers 20` command
    - Verify all five adapters are initialized and executed
    - Verify final stats report includes all rejection categories
    - Verify database tables are created before ingestion
    - _Requirements: 13.1, 13.2, 13.3, 13.5, 13.6_

- [ ] 15. Final checkpoint - Ensure all tests pass
  - Run full test suite with pytest
  - Verify minimum 40 meaningful Phase 6 tests with all critical behaviors covered (target approximately 45-50 tests)
  - Verify all tests use mocked fixtures (no live network dependencies)
  - Ensure coverage for all five news source adapters
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 16. Add README documentation for news vertical
  - [ ] 16.1 Document news vertical usage and architecture
    - Document usage: `python -m src.main --vertical news --target 1000 --workers 50`
    - Document five news sources with endpoints and descriptions
    - Document anti-hallucination guarantees (missing data → rejection, never fabrication)
    - Document freshness filtering (24-hour window with configurable clock skew tolerance)
    - Document full-text extraction requirement (minimum 100 characters)
    - Document deduplication strategy (URL-based per source, content-hash for raw documents)
    - Document deterministic content-addressable full-text storage (migration-ready for S3/MinIO)
    - Add note that news sources are unreachable from sandbox (egress restricted to pypi.org, npmjs.org, github.com)
    - Add note that adapters will be verified against live sources post-deployment
    - _Requirements: 18.2, 18.3, 19.1, 19.2, 19.3, 19.4, 19.6_

## Notes

- All critical test tasks are now MANDATORY (no optional "*" markers on assessment-critical tests)
- All network operations use the shared HTTP_Client for connection pooling, retry logic, and bounded concurrency
- News adapters DO NOT implement independent retry loops - all retry/backoff/jitter stays centralized in AsyncHttpClient
- Adapters classify terminal errors (BlockedSourceError, PayloadTooLargeError) and update pipeline statistics/logging only
- Date extraction reuses the existing Date_Engine with priority chain (RSS pubDate → JSON-LD → meta tags → time tags → visible text)
- Raw document provenance is preserved for all fetched articles, even when extraction fails
- Extraction failures are tracked separately from valid records (extraction_failed counter distinct from valid_records)
- Clock skew tolerance (default 60 seconds) handles minor server clock drift, not genuinely future-scheduled articles
- All tests use mocked fixtures to avoid live network dependencies in sandbox environment
- News adapters will be verified against live endpoints post-deployment to environment with normal internet egress
- Full-text storage uses deterministic content-addressable mechanism (SHA-256 hash-based location) for demo, migration-ready for S3/MinIO
- Raw HTML preserved separately in RawDocumentRepository with content-hash deduplication
- Target approximately 45-50 meaningful Phase 6 tests (minimum 40) with all critical behaviors covered

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "1.4", "1.5"] },
    { "id": 1, "tasks": ["2.1", "3.1"] },
    { "id": 2, "tasks": ["2.2", "3.2", "5.1"] },
    { "id": 3, "tasks": ["5.2", "6.1"] },
    { "id": 4, "tasks": ["6.2", "7.1", "7.2", "7.3", "7.4"] },
    { "id": 5, "tasks": ["7.5", "9.1"] },
    { "id": 6, "tasks": ["9.2", "9.3", "11.1", "11.2", "11.3"] },
    { "id": 7, "tasks": ["9.4", "10.1", "13.1"] },
    { "id": 8, "tasks": ["10.2", "12.1", "12.2", "14.1"] },
    { "id": 9, "tasks": ["14.2", "16.1"] }
  ]
}
```
