"""Entity resolution engine tests (Phase 12, assessment Section 22)."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.resolution import (
    METHOD_ALIAS,
    METHOD_CREATED,
    METHOD_FUZZY,
    METHOD_NORMALIZED_EXACT,
    METHOD_UNRESOLVED,
    EntityResolver,
)
from src.storage.models import CanonicalEntity, EntityAlias, EntityMappingLog


async def _count(session: AsyncSession, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


@pytest.mark.asyncio
async def test_first_sighting_creates_canonical_entity(db_session: AsyncSession) -> None:
    resolver = EntityResolver(db_session)
    result = await resolver.resolve("OpenAI", source_url="https://example.com/job/1")

    assert result.method == METHOD_CREATED
    assert result.created is True
    assert result.confidence == 1.0
    assert result.canonical_entity_id is not None
    assert result.normalized_name == "openai"
    assert await _count(db_session, CanonicalEntity) == 1


@pytest.mark.asyncio
async def test_normalized_exact_match_reuses_entity(db_session: AsyncSession) -> None:
    resolver = EntityResolver(db_session)
    first = await resolver.resolve("OpenAI")
    second = await resolver.resolve("  OpenAI, Inc. ")

    assert second.method == METHOD_NORMALIZED_EXACT
    assert second.confidence == 1.0
    assert second.canonical_entity_id == first.canonical_entity_id
    assert await _count(db_session, CanonicalEntity) == 1


@pytest.mark.asyncio
async def test_fuzzy_match_links_and_registers_alias(db_session: AsyncSession) -> None:
    resolver = EntityResolver(db_session)
    first = await resolver.resolve("Anthropic Research")
    fuzzy = await resolver.resolve("Anthropic Resarch")  # typo, above threshold

    assert fuzzy.method == METHOD_FUZZY
    assert fuzzy.canonical_entity_id == first.canonical_entity_id
    assert 0.9 <= fuzzy.confidence < 1.0
    assert await _count(db_session, CanonicalEntity) == 1
    assert await _count(db_session, EntityAlias) == 1


@pytest.mark.asyncio
async def test_registered_alias_resolves_as_alias_on_next_sighting(db_session: AsyncSession) -> None:
    resolver = EntityResolver(db_session)
    first = await resolver.resolve("Anthropic Research")
    await resolver.resolve("Anthropic Resarch")
    again = await resolver.resolve("Anthropic Resarch")

    assert again.method == METHOD_ALIAS
    assert again.confidence == pytest.approx(0.99)
    assert again.canonical_entity_id == first.canonical_entity_id
    # No second alias row for a repeat sighting.
    assert await _count(db_session, EntityAlias) == 1


@pytest.mark.asyncio
async def test_distinct_companies_are_not_merged(db_session: AsyncSession) -> None:
    resolver = EntityResolver(db_session)
    a = await resolver.resolve("OpenAI")
    b = await resolver.resolve("Cohere")
    c = await resolver.resolve("Hugging Face")

    ids = {a.canonical_entity_id, b.canonical_entity_id, c.canonical_entity_id}
    assert len(ids) == 3
    assert await _count(db_session, CanonicalEntity) == 3


@pytest.mark.asyncio
async def test_near_miss_below_threshold_is_not_merged(db_session: AsyncSession) -> None:
    """A false merge corrupts the dataset -- below threshold we split."""
    resolver = EntityResolver(db_session, fuzzy_threshold=99.0)
    first = await resolver.resolve("Scale AI")
    second = await resolver.resolve("Scala AI")

    assert second.canonical_entity_id != first.canonical_entity_id
    assert second.method == METHOD_CREATED
    assert second.review_candidate is not None  # flagged for human review
    assert await _count(db_session, CanonicalEntity) == 2


@pytest.mark.asyncio
async def test_unusable_name_is_unresolved_not_fabricated(db_session: AsyncSession) -> None:
    resolver = EntityResolver(db_session)
    for bad in ("", "   ", None, "!!!", "x"):
        result = await resolver.resolve(bad)
        assert result.method == METHOD_UNRESOLVED
        assert result.canonical_entity_id is None
        assert result.confidence == 0.0

    assert await _count(db_session, CanonicalEntity) == 0
    # Every rejection is still audited.
    assert await _count(db_session, EntityMappingLog) == 5


@pytest.mark.asyncio
async def test_every_decision_is_written_to_mapping_log(db_session: AsyncSession) -> None:
    resolver = EntityResolver(db_session)
    await resolver.resolve("OpenAI", source_url="https://example.com/a")
    await resolver.resolve("OpenAI Inc", source_url="https://example.com/b")
    await resolver.resolve("Cohere", source_url="https://example.com/c")

    rows = (await db_session.execute(select(EntityMappingLog))).scalars().all()
    assert len(rows) == 3
    methods = [r.method for r in rows]
    assert methods == [METHOD_CREATED, METHOD_NORMALIZED_EXACT, METHOD_CREATED]
    assert {r.source_url for r in rows} == {
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    }
    for row in rows:
        assert row.raw_name
        assert 0.0 <= row.confidence <= 1.0


@pytest.mark.asyncio
async def test_create_if_missing_false_leaves_unresolved(db_session: AsyncSession) -> None:
    resolver = EntityResolver(db_session)
    result = await resolver.resolve("Brand New Co", create_if_missing=False)

    assert result.method == METHOD_UNRESOLVED
    assert result.canonical_entity_id is None
    assert await _count(db_session, CanonicalEntity) == 0


@pytest.mark.asyncio
async def test_index_is_warmed_from_existing_rows(db_session: AsyncSession) -> None:
    """A fresh resolver in a later run must reuse entities already in the DB."""
    first_run = EntityResolver(db_session)
    original = await first_run.resolve("OpenAI")

    second_run = EntityResolver(db_session)
    again = await second_run.resolve("OpenAI, Inc.")

    assert again.canonical_entity_id == original.canonical_entity_id
    assert again.method == METHOD_NORMALIZED_EXACT
    assert await _count(db_session, CanonicalEntity) == 1


@pytest.mark.asyncio
async def test_log_decisions_can_be_disabled(db_session: AsyncSession) -> None:
    resolver = EntityResolver(db_session, log_decisions=False)
    await resolver.resolve("OpenAI")
    assert await _count(db_session, EntityMappingLog) == 0
    assert await _count(db_session, CanonicalEntity) == 1
