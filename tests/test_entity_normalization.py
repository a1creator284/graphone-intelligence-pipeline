"""Unit tests for deterministic entity-name normalization (Phase 12)."""
from __future__ import annotations

import pytest

from src.resolution.normalize import (
    normalization_is_usable,
    normalize_entity_name,
    strip_accents,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("OpenAI", "openai"),
        ("  OpenAI  ", "openai"),
        ("OpenAI, Inc.", "openai"),
        ("OpenAI Inc", "openai"),
        ("Anthropic PBC", "anthropic"),
        ("Hugging Face", "hugging face"),
        ("Hugging-Face", "hugging face"),
        ("Hugging_Face", "hugging face"),
        ("HUGGING FACE", "hugging face"),
        ("Scale AI, LLC", "scale ai"),
        ("DeepMind Technologies Limited", "deepmind technologies"),
        ("The Allen Institute", "allen institute"),
        ("Acme Co Ltd", "acme"),
        ("Stability AI Ltd.", "stability ai"),
        ("R&D Labs", "r and d labs"),
        ("Cohere GmbH", "cohere"),
    ],
)
def test_normalization_cases(raw: str, expected: str) -> None:
    assert normalize_entity_name(raw) == expected


def test_accents_are_folded() -> None:
    assert strip_accents("Föö") == "Foo"
    assert normalize_entity_name("Créative AI") == "creative ai"


def test_empty_and_none_produce_empty_key() -> None:
    assert normalize_entity_name(None) == ""
    assert normalize_entity_name("") == ""
    assert normalize_entity_name("   ") == ""
    assert normalize_entity_name("!!!") == ""


def test_legal_suffix_inside_name_is_not_stripped() -> None:
    """'Inc' as a word inside the name must survive -- only trailing tokens go."""
    assert normalize_entity_name("Incredible AI") == "incredible ai"
    assert normalize_entity_name("Corporate Vision") == "corporate vision"


def test_suffix_only_name_is_not_emptied() -> None:
    """A name that is *only* a suffix keeps it -- we never return nothing
    when the source gave us something."""
    assert normalize_entity_name("Inc") == "inc"


def test_usability_gate_rejects_single_characters() -> None:
    assert normalization_is_usable("openai") is True
    assert normalization_is_usable("ai") is True
    assert normalization_is_usable("x") is False
    assert normalization_is_usable("") is False


def test_normalization_is_idempotent() -> None:
    once = normalize_entity_name("OpenAI, Inc.")
    assert normalize_entity_name(once) == once
