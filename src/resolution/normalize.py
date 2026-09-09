"""
Deterministic entity-name normalization (Phase 12, assessment Section 22).

Normalization is the *first* and cheapest stage of entity resolution: two
names that normalize to the same string are the same entity with confidence
1.0, no fuzzy matching required.

Design rules:
- Pure, deterministic, side-effect free. No network, no LLM, no guessing.
- Never invents information. If a name is empty/whitespace-only after
  normalization we return an empty string and let the caller reject it,
  rather than substituting a placeholder.
- Legal-suffix stripping is conservative and list-driven: only well-known
  incorporation suffixes are removed, and only when they appear as trailing
  tokens. "Inc" inside "Incredible AI" is never stripped.
"""
from __future__ import annotations

import re
import unicodedata

# Trailing incorporation / legal-form suffixes. Compared token-wise against
# the tail of the name after punctuation stripping, so "Acme, Inc." and
# "Acme Inc" both reduce to "acme".
LEGAL_SUFFIXES: frozenset[str] = frozenset(
    {
        "inc",
        "incorporated",
        "llc",
        "l l c",
        "ltd",
        "limited",
        "plc",
        "corp",
        "corporation",
        "co",
        "company",
        "gmbh",
        "ag",
        "sa",
        "sas",
        "sarl",
        "bv",
        "nv",
        "ab",
        "as",
        "oy",
        "kk",
        "pte",
        "pty",
        "srl",
        "spa",
        "kft",
        "sp z oo",
        "ug",
        "llp",
        "lp",
        "pbc",
    }
)

# Noise tokens some job boards append to the employer field.
TRAILING_NOISE: frozenset[str] = frozenset({"the", "group", "holdings", "holding"})

_WHITESPACE_RE = re.compile(r"\s+")
# Keep alphanumerics and spaces; everything else becomes a space so that
# "Hugging-Face" and "Hugging Face" converge.
_NON_ALNUM_RE = re.compile(r"[^0-9a-z ]+")


def strip_accents(value: str) -> str:
    """NFKD-decompose and drop combining marks ("Fören" -> "Foren")."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_entity_name(raw_name: str | None) -> str:
    """Reduce a raw entity name to its comparison key.

    Returns an empty string when the input carries no usable signal. The
    caller decides what to do with that (we never fabricate a name).

    >>> normalize_entity_name("  OpenAI, Inc. ")
    'openai'
    >>> normalize_entity_name("Hugging-Face")
    'hugging face'
    """
    if not raw_name:
        return ""

    value = strip_accents(str(raw_name))
    value = value.casefold()
    # Normalize common ampersand/plus spellings before punctuation stripping
    # so "R&D Labs" and "R and D Labs" converge.
    value = value.replace("&", " and ")
    value = _NON_ALNUM_RE.sub(" ", value)
    value = _WHITESPACE_RE.sub(" ", value).strip()
    if not value:
        return ""

    tokens = value.split(" ")

    # Strip trailing legal suffixes repeatedly ("Acme Co Ltd" -> "acme").
    while len(tokens) > 1 and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()

    # Strip a single leading article ("The Allen Institute" -> "allen institute").
    if len(tokens) > 1 and tokens[0] == "the":
        tokens.pop(0)

    while len(tokens) > 1 and tokens[-1] in TRAILING_NOISE:
        tokens.pop()

    return " ".join(tokens)


def normalization_is_usable(normalized: str, *, min_length: int = 2) -> bool:
    """A normalized key is only safe to resolve on if it has real signal.

    Single-character keys ("a", "x") produce garbage fuzzy matches, so they
    are treated as unresolvable rather than being force-matched.
    """
    return len(normalized) >= min_length
