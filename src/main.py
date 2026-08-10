"""
CLI entrypoint.

python -m src.main --vertical research --workers 50
python -m src.main --vertical all
python -m src.main --export

Status: --vertical research is wired end-to-end (arXiv + Papers With Code +
GitHub enrichment -> validation -> Postgres). Other verticals (news, jobs,
startups, products) report their registered sources but do not yet crawl --
those adapters land in Phases 6-11. See README "Project status".
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from src.config.logging import configure_logging, get_logger
from src.config.sources import Vertical, get_sources_for_vertical
from src.storage.database import init_engine
from src.storage.models import Base

logger = get_logger(component="cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.main", description="GraphOne intelligence ingestion pipeline")
    parser.add_argument("--vertical", choices=[v.value for v in Vertical] + ["all"], help="Which vertical to run")
    parser.add_argument("--target", type=int, default=None, help="Target record count for this run")
    parser.add_argument("--workers", type=int, default=None, help="Override MAX_CONCURRENCY for this run")
    parser.add_argument("--dry-run", action="store_true", help="Discover/validate without writing to the database")
    parser.add_argument("--export", action="store_true", help="Export current DB contents to CSV/XLSX/Sheets")
    return parser


async def _run_research(args: argparse.Namespace) -> None:
    from src.config.settings import get_settings
    from src.pipeline.research import run_research_pipeline
    from src.storage.database import get_session_factory

    settings = get_settings()
    engine = init_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = get_session_factory()
    async with factory() as session:
        result = await run_research_pipeline(
            session,
            target=args.target or 1000,
            max_concurrency=args.workers or settings.max_concurrency,
        )
    logger.info(
        "research_run_summary",
        target=result.target,
        discovered=result.discovered,
        valid_records=result.valid_records,
        duplicates=result.duplicates,
        rejected=result.rejected,
        github_enriched=result.github_enriched,
        by_source=result.by_source,
    )
    if result.target and result.valid_records < result.target:
        logger.info(
            "target_not_fully_met",
            target=result.target,
            valid_records=result.valid_records,
            note="Per Section 48 (No Fake Success): reporting the real count rather than padding.",
        )


async def _run(args: argparse.Namespace) -> int:
    if args.export:
        logger.info("export_not_yet_wired", note="Export module lands in Phase 13")
        return 0

    if not args.vertical:
        build_parser().print_help()
        return 1

    verticals = list(Vertical) if args.vertical == "all" else [Vertical(args.vertical)]
    for vertical in verticals:
        sources = get_sources_for_vertical(vertical)
        logger.info(
            "vertical_status",
            vertical=vertical.value,
            registered_sources=[s.name for s in sources],
            dry_run=args.dry_run,
        )
        if vertical == Vertical.RESEARCH and not args.dry_run:
            await _run_research(args)
        elif vertical != Vertical.RESEARCH:
            logger.info(
                "vertical_not_yet_wired",
                vertical=vertical.value,
                note="Adapter execution for this vertical lands in a later phase.",
            )
    return 0


def main() -> None:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args()
    exit_code = asyncio.run(_run(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
