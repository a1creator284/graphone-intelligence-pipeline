"""Dashboard-side importer for the final assessment workbook.

This package exists so the dashboard can be pointed at the *real* assessment
dataset (``submission/graphone_final.xlsx``) instead of a hand-made demo
dataset, without touching the ingestion pipeline in ``src/``.

Design constraints that shaped this package:

* It is **read-only** with respect to the workbook and with respect to
  ``src/`` -- it imports the pipeline's own ORM models
  (``src.storage.models``) rather than defining a second, competing schema.
* It **validates the entire workbook before any database write**. A failed
  validation performs zero writes (see :mod:`dashboard.importer.validate`).
* It writes inside **one transaction**, so a mid-import integrity error
  leaves no partial dataset behind (see :mod:`dashboard.importer.loader`).
* It **never fabricates** source data. Anything the workbook does not export
  is either derived by an explicitly documented application policy
  (``entity_type``) or left absent and documented as a limitation
  (``entity_aliases``, ``crawl_runs``, raw payload bytes).
"""
from __future__ import annotations

from dashboard.importer.validate import (
    EXPECTED_SHEET_COUNTS,
    SUPPORTED_ENTITY_TYPES,
    EntityTypeUnsupportedError,
    WorkbookDataset,
    WorkbookValidationError,
    validate_workbook,
)

__all__ = [
    "EXPECTED_SHEET_COUNTS",
    "SUPPORTED_ENTITY_TYPES",
    "EntityTypeUnsupportedError",
    "WorkbookDataset",
    "WorkbookValidationError",
    "validate_workbook",
]
