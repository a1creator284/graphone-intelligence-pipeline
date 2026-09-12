"""Command-line entry point for importing the final assessment workbook.

Usage (run from the repository root)::

    # 1. Dry run -- validates the whole workbook, touches no database at all.
    python -m dashboard.importer --validate-only

    # 2. Real import, replacing the local demo dataset in one transaction.
    python -m dashboard.importer --replace

The database URL comes from the environment (``DATABASE_URL``) via the
pipeline's own ``Settings`` object -- no credential is hardcoded here. On the
developer's Mac the pipeline default already points at
``postgresql+asyncpg://graphone:graphone@localhost:5432/graphone``.

``--replace`` is deliberately explicit: without it the importer upserts onto
whatever is already in the database (the idempotent re-run path) and will not
delete anything.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dashboard.importer.loader import UNTOUCHED_TABLES, import_dataset, table_counts
from dashboard.importer.validate import (
    EXPECTED_SHEET_COUNTS,
    WorkbookValidationError,
    summarise,
    validate_workbook,
)

DEFAULT_WORKBOOK = Path("submission/graphone_final.xlsx")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dashboard.importer",
        description="Import the final six-tab assessment workbook into the dashboard database.",
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=DEFAULT_WORKBOOK,
        help=f"path to the six-tab XLSX export (default: {DEFAULT_WORKBOOK})",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy async URL. Defaults to DATABASE_URL from the environment/.env.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate the workbook and exit without opening a database connection",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help=(
            "delete existing rows from the nine importer-owned tables before loading, so the final "
            "dataset is not mixed with a demo dataset. Runs inside the same rollback-capable transaction."
        ),
    )
    parser.add_argument(
        "--skip-count-check",
        action="store_true",
        help="do not assert the verified per-sheet row counts (for non-final workbooks)",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable JSON report")
    return parser


async def _run_import(args: argparse.Namespace, dataset) -> dict:
    # Imported lazily so --validate-only never constructs an engine.
    from src.storage.database import engine_scope, get_session_factory

    async with engine_scope(args.database_url):
        factory = get_session_factory()
        async with factory() as session:
            before = await table_counts(session)
            await session.rollback()
            result = await import_dataset(session, dataset, replace=args.replace)
    payload = result.as_dict()
    payload["counts_before"] = before
    return payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    expected = None if args.skip_count_check else EXPECTED_SHEET_COUNTS
    try:
        dataset = validate_workbook(args.workbook, expected_counts=expected)
    except WorkbookValidationError as exc:
        print(f"VALIDATION FAILED -- no database writes were attempted.\n{exc}", file=sys.stderr)
        return 2

    if args.validate_only:
        if args.json:
            print(json.dumps(dataset.report, indent=2, default=str))
        else:
            print(summarise(dataset))
            print("\nvalidation OK -- no database connection was opened.")
        return 0

    try:
        payload = asyncio.run(_run_import(args, dataset))
    except Exception as exc:  # noqa: BLE001 - surface the real cause to the operator
        print(
            "IMPORT FAILED -- the transaction was rolled back; no partial data was committed.\n"
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps({"validation": dataset.report, "import": payload}, indent=2, default=str))
        return 0

    print(summarise(dataset))
    print(f"\nmode: {payload['mode']}")
    if payload["deleted"]:
        print(f"deleted (importer-owned tables only): {payload['deleted']}")
    print(f"inserted: {payload['inserted']}")
    print(f"updated:  {payload['updated']}")
    print("final counts:")
    for key, value in payload["final_counts"].items():
        print(f"  {key:22s} {value}")
    print(f"never modified: {', '.join(UNTOUCHED_TABLES)}")
    for note in payload["notes"]:
        print(f"note: {note}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
