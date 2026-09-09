"""Entity resolution / deduplication (Phase 12)."""
from src.resolution.normalize import (
    LEGAL_SUFFIXES,
    normalization_is_usable,
    normalize_entity_name,
    strip_accents,
)
from src.resolution.resolver import (
    DEFAULT_FUZZY_THRESHOLD,
    METHOD_ALIAS,
    METHOD_CREATED,
    METHOD_FUZZY,
    METHOD_NORMALIZED_EXACT,
    METHOD_UNRESOLVED,
    EntityResolver,
    ResolutionResult,
)

__all__ = [
    "DEFAULT_FUZZY_THRESHOLD",
    "LEGAL_SUFFIXES",
    "METHOD_ALIAS",
    "METHOD_CREATED",
    "METHOD_FUZZY",
    "METHOD_NORMALIZED_EXACT",
    "METHOD_UNRESOLVED",
    "EntityResolver",
    "ResolutionResult",
    "normalization_is_usable",
    "normalize_entity_name",
    "strip_accents",
]
