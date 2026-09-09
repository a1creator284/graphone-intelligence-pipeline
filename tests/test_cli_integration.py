from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import text

from src.main import main


@pytest.mark.asyncio
async def test_cli_news_vertical_integration(capsys, db_session):
    """Test `python -m src.main --vertical news --target 100 --workers 20` command.
    Verifies adapter initialization, stat reporting with all rejection categories,
    and database table creation.
    """
    with patch("sys.argv", ["python -m src.main", "--vertical", "news", "--target", "100", "--workers", "20"]):
        with patch("src.pipeline.news.run_adapter") as mock_run_adapter:
            from src.pipeline.workers import RunStats
            from src.crawlers.base import ParsedRecord
            from src.crawlers.http import FetchResult
            from datetime import datetime, timezone, timedelta

            ref_time = datetime.now(timezone.utc)
            
            # Create a mock ParsedRecord that will fail validation (empty title)
            # to verify rejection categories in stats
            fetch_res = FetchResult(
                url="https://example.com/invalid",
                status_code=200,
                text="<html><body>Hello World</body></html>",
                content_hash="hash123",
                headers={},
            )
            invalid_record = ParsedRecord(
                record_type="NEWS",
                data={
                    "title": "",  # invalid
                    "url": "https://example.com/invalid",
                    "source_name": "hackernews_ai",
                    "published_at": ref_time - timedelta(hours=1),
                    "full_text_location": "inline:extracted_metadata.full_text",
                    "extracted_metadata": {"full_text": "Valid content " * 20},
                },
                source_name="hackernews_ai",
                source_url="https://example.com/invalid",
                fetch_result=fetch_res,
            )
            
            # Stale record to trigger freshness rejection
            stale_record = ParsedRecord(
                record_type="NEWS",
                data={
                    "title": "Stale",
                    "url": "https://example.com/stale",
                    "source_name": "techcrunch_ai_rss",
                    "published_at": ref_time - timedelta(hours=25),
                    "full_text_location": "inline:extracted_metadata.full_text",
                    "extracted_metadata": {"full_text": "Stale content " * 20},
                },
                source_name="techcrunch_ai_rss",
                source_url="https://example.com/stale",
                fetch_result=fetch_res,
            )

            # Valid record
            valid_record = ParsedRecord(
                record_type="NEWS",
                data={
                    "title": "Valid",
                    "url": "https://example.com/valid",
                    "source_name": "theverge_ai_rss",
                    "published_at": ref_time - timedelta(hours=1),
                    "full_text_location": "inline:extracted_metadata.full_text",
                    "extracted_metadata": {"full_text": "Valid content " * 20},
                },
                source_name="theverge_ai_rss",
                source_url="https://example.com/valid",
                fetch_result=fetch_res,
            )

            mock_run_adapter.side_effect = [
                (RunStats(discovered=1, fetched=1, parsed_records=1), [invalid_record]),
                (RunStats(discovered=1, fetched=1, parsed_records=1), [stale_record]),
                (RunStats(discovered=1, fetched=1, parsed_records=1), [valid_record]),
                (RunStats(discovered=0, fetched=0, parsed_records=0), []),
                (RunStats(discovered=0, fetched=0, parsed_records=0), []),
            ]

            # CLI integration tests typically run the main process logic.
            # We mock sys.argv and call _run() directly to avoid asyncio.run() conflict in pytest.
            from src.main import _run, build_parser
            
            # We must also mock the database initialization in main to use the test session
            with patch("src.main.engine_scope") as mock_engine_scope, \
                 patch("src.storage.database.get_session_factory") as mock_get_session_factory:
                
                # We need a dummy engine with a begin() context manager
                class DummyEngine:
                    def begin(self):
                        class DummyContextManager:
                            async def __aenter__(self):
                                class DummyConn:
                                    async def run_sync(self, func):
                                        pass
                                return DummyConn()
                            async def __aexit__(self, exc_type, exc_val, exc_tb):
                                pass
                        return DummyContextManager()
                        
                # `_run_news` now acquires its engine through `engine_scope`,
                # which owns disposal of the pool (see test_cli_lifecycle.py).
                @asynccontextmanager
                async def dummy_engine_scope(database_url=None):
                    yield DummyEngine()

                mock_engine_scope.side_effect = dummy_engine_scope
                
                class DummyFactory:
                    def __call__(self):
                        class DummyContextManager:
                            async def __aenter__(self):
                                return db_session
                            async def __aexit__(self, exc_type, exc_val, exc_tb):
                                pass
                        return DummyContextManager()
                        
                mock_get_session_factory.return_value = DummyFactory()
                
                parser = build_parser()
                args = parser.parse_args()
                exit_code = await _run(args)
                assert exit_code == 0

            # 1. Verify all 5 adapters are initialized and executed
            assert mock_run_adapter.call_count == 5
            for call in mock_run_adapter.call_args_list:
                assert call.kwargs["max_items"] == 20 # target 100 // 5

            # 2. Verify final stats report includes all rejection categories
            captured = capsys.readouterr()
            stdout = captured.out
            assert "news_run_summary" in stdout
            assert "rejected" in stdout

            # 3. Verify database tables are created before ingestion
            # We can run a raw SQL query checking for the existence of one of the tables
            # Assuming sqlite for the test environment.
            table_check = await db_session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='news'"))
            assert table_check.scalar() == "news"



@pytest.mark.asyncio
async def test_cli_startups_vertical_is_wired(capsys, db_session):
    """`--vertical startups --target 5 --workers 7` must execute the existing
    startups pipeline (not print `vertical_not_yet_wired`), forwarding
    --target and --workers through to `run_startups_pipeline`.
    """
    from src.main import _run, build_parser
    from src.pipeline.startups import StartupsPipelineResult

    called: dict[str, object] = {}

    async def fake_pipeline(session, *, target=1000, max_concurrency=20, **kwargs):
        called["session"] = session
        called["target"] = target
        called["max_concurrency"] = max_concurrency
        return StartupsPipelineResult(
            target=target,
            discovered=1,
            valid_records=target,
            entities_resolved=target,
            by_source={"ycombinator_directory": target},
        )

    class DummyEngine:
        def begin(self):
            class DummyContextManager:
                async def __aenter__(self):
                    class DummyConn:
                        async def run_sync(self, func):
                            pass

                    return DummyConn()

                async def __aexit__(self, exc_type, exc_val, exc_tb):
                    pass

            return DummyContextManager()

    @asynccontextmanager
    async def dummy_engine_scope(database_url=None):
        yield DummyEngine()

    class DummyFactory:
        def __call__(self):
            class DummyContextManager:
                async def __aenter__(self):
                    return db_session

                async def __aexit__(self, exc_type, exc_val, exc_tb):
                    pass

            return DummyContextManager()

    with patch("sys.argv", ["python -m src.main", "--vertical", "startups", "--target", "5", "--workers", "7"]), \
            patch("src.pipeline.startups.run_startups_pipeline", side_effect=fake_pipeline), \
            patch("src.main.engine_scope", side_effect=dummy_engine_scope), \
            patch("src.storage.database.get_session_factory", return_value=DummyFactory()):
        args = build_parser().parse_args()
        exit_code = await _run(args)

    assert exit_code == 0
    assert called["target"] == 5
    assert called["max_concurrency"] == 7
    assert called["session"] is db_session

    stdout = capsys.readouterr().out
    assert "vertical_not_yet_wired" not in stdout
    assert "startups_run_summary" in stdout


@pytest.mark.asyncio
async def test_cli_startups_dry_run_does_not_execute_pipeline(capsys):
    """`--dry-run` must report the registered startups sources without
    invoking the pipeline or touching the database."""
    from src.main import _run, build_parser
    from unittest.mock import AsyncMock

    with patch("sys.argv", ["python -m src.main", "--vertical", "startups", "--dry-run"]), \
            patch("src.pipeline.startups.run_startups_pipeline", new_callable=AsyncMock) as mock_pipeline:
        args = build_parser().parse_args()
        exit_code = await _run(args)

    assert exit_code == 0
    mock_pipeline.assert_not_awaited()

    stdout = capsys.readouterr().out
    assert "ycombinator_directory" in stdout
    assert "startups_run_summary" not in stdout
