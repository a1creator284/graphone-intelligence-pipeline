"""Phase 2 regressions. Fixtures only; never seed a running dashboard database.

Run alongside the original suite: python -m pytest tests dashboard/backend
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select, text

from dashboard.backend.app.routers.details import activity_key, recent_activity, utc_timestamp
from dashboard.backend.app.schemas import ActivityItem
from src.storage.models import CanonicalEntity, EntityAlias, Job, News, Product, RawDocument, ResearchPaper, Startup
from tests.test_dashboard_api import api_session, client, _seed, _seed_entities, NOW

ROUTES = ["startups", "products", "research-papers", "news", "jobs"]
MODELS = [Startup, Product, ResearchPaper, News, Job]


@pytest.mark.parametrize("route,model", zip(ROUTES, MODELS))
async def test_details_provenance_and_readonly(client, api_session, route, model):
    await _seed(api_session)
    row = await api_session.scalar(select(model))
    raw = await api_session.scalar(select(RawDocument))
    row.raw_document_id = raw.id
    if hasattr(row, "canonical_entity_id"):
        entity = await api_session.scalar(select(CanonicalEntity))
        row.canonical_entity_id = entity.id
    await api_session.commit()
    response = await client.get(f"/api/{route}/{row.id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["id"] == str(row.id)
    assert detail["collected_at"]
    assert detail["source_name"] == row.source_name
    assert detail["provenance"]["source_url"] == raw.source_url
    assert detail["provenance"]["content_hash"] == raw.content_hash
    assert "raw_content_location" not in detail["provenance"]
    if hasattr(row, "canonical_entity_id"):
        assert detail["canonical_entity"]["id"] == str(entity.id)
    for verb in (client.post, client.put, client.delete):
        assert (await verb(f"/api/{route}/{row.id}")).status_code == 405


@pytest.mark.parametrize("route", ROUTES + ["entities"])
async def test_detail_errors(client, route):
    assert (await client.get(f"/api/{route}/{uuid4()}")).status_code == 404
    assert (await client.get(f"/api/{route}/not-a-uuid")).status_code == 422
    assert (await client.get(f"/api/{route}/' OR 1=1 --")).status_code == 422


@pytest.mark.parametrize("route,model", zip(ROUTES, MODELS))
async def test_missing_optional_context_is_honest(client, api_session, route, model):
    await _seed(api_session)
    row = await api_session.scalar(select(model))
    detail = (await client.get(f"/api/{route}/{row.id}")).json()
    assert detail["provenance"] is None
    assert detail["canonical_entity"] is None
    assert detail["raw_document_id"] is None


async def test_entity_detail_bounds_counts_and_orphans(client, api_session):
    entity, orphan = await _seed_entities(api_session)
    for i in range(25):
        api_session.add(EntityAlias(canonical_entity_id=entity.id, alias=f"Alias {i:02}", normalized_alias=f"alias {i}"))
        api_session.add(Product(canonical_entity_id=entity.id, product_name=f"Product {i}", startup_name="Anthropic", source_name="test", source_url=f"https://example.com/{i}"))
    await api_session.commit()
    detail = (await client.get(f"/api/entities/{entity.id}?relationship_limit=2")).json()
    assert detail["canonical_name"] == "Anthropic"
    assert detail["total_records"] == 28
    assert detail["product_count"] == 26
    assert detail["alias_count"] == 26
    assert len(detail["aliases"]) == len(detail["products"]) == 2
    assert len(detail["startups"]) == len(detail["jobs"]) == 1
    assert detail["relationship_limit"] == 2
    empty = (await client.get(f"/api/entities/{orphan.id}")).json()
    assert empty["total_records"] == 0
    assert empty["aliases"] == empty["products"] == empty["jobs"] == empty["startups"] == []
    listed = (await client.get("/api/entities?q=Anthropic")).json()["items"][0]
    assert listed["alias_count"] == 26 and len(listed["aliases"]) == 20
    for limit in (0, -1, 51):
        assert (await client.get(f"/api/entities/{entity.id}?relationship_limit={limit}")).status_code == 422


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("sort", ["recent", "oldest", "name", "source"])
async def test_sort_search_pagination_compatibility(client, api_session, route, sort):
    await _seed(api_session, startups=3, products=3, papers=3, jobs=3, news=3)
    first = (await client.get(f"/api/{route}", params={"sort": sort, "limit": 1})).json()
    second = (await client.get(f"/api/{route}", params={"sort": sort, "limit": 1, "offset": 1})).json()
    assert first["total"] == second["total"] == 3
    assert first["has_more"] is True
    assert first["items"][0]["id"] != second["items"][0]["id"]
    filtered = (await client.get(f"/api/{route}", params={"sort": sort, "q": "  1 ", "limit": 1})).json()
    assert filtered["total"] == 1 and filtered["has_more"] is False
    assert (await client.get(f"/api/{route}", params={"sort": "source_name; DROP TABLE products"})).status_code == 422


async def test_null_names_last_and_stable_pages(client, api_session):
    for i, name in enumerate([None, "Beta", "Alpha", "Alpha"]):
        api_session.add(Product(id=UUID(int=i+1), product_name=name, startup_name="Vendor", source_name="test", source_url=f"https://example.com/{i}", collected_at=NOW))
    await api_session.commit()
    rows = (await client.get("/api/products?sort=name")).json()["items"]
    assert [r["product_name"] for r in rows] == ["Alpha", "Alpha", "Beta", None]
    assert rows[0]["id"] < rows[1]["id"]
    assert [r["id"] for r in rows] == [(await client.get(f"/api/products?sort=name&limit=1&offset={i}")).json()["items"][0]["id"] for i in range(4)]


async def test_timestamp_sort_uses_instants_not_offset_strings(client, api_session):
    await _seed(api_session, startups=3, products=0, papers=0, jobs=0, news=0, entities=0, raw=0)
    # SQLite may carry legacy ISO timestamps with offsets or naive UTC text.
    for i, stamp in enumerate(["2026-08-10 12:00:00+05:00", "2026-08-10 08:00:00", "2026-08-10 06:30:00-02:00"]):
        await api_session.execute(text("UPDATE startups SET collected_at = :stamp WHERE entity_name = :name"), {"stamp": stamp, "name": f"Startup {i}"})
    await api_session.commit()
    api_session.expire_all()
    for sort, order in [("recent", [2, 1, 0]), ("oldest", [0, 1, 2])]:
        rows = (await client.get(f"/api/startups?sort={sort}")).json()["items"]
        assert [r["entity_name"] for r in rows] == [f"Startup {i}" for i in order]
    activity = (await client.get("/api/dashboard/recent-activity?limit=2")).json()["items"]
    assert [r["title"] for r in activity] == ["Startup 2", "Startup 1"]
    assert all(r["collected_at"].endswith("Z") for r in activity)


async def test_activity_real_records_only_and_bounded(client, api_session):
    assert (await client.get("/api/dashboard/recent-activity")).json()["items"] == []
    await _seed(api_session, entities=0, raw=0)
    response = (await client.get("/api/dashboard/recent-activity?limit=50")).json()
    assert len(response["items"]) == 5
    assert {r["vertical"] for r in response["items"]} == set(ROUTES)
    assert all(r["source_url"].startswith("https://") for r in response["items"])
    top = (await client.get("/api/dashboard/recent-activity?limit=2")).json()["items"]
    assert top == response["items"][:2]
    for limit in (0, -1, 51):
        assert (await client.get(f"/api/dashboard/recent-activity?limit={limit}")).status_code == 422


def test_null_naive_and_aware_timestamp_keys():
    dates = [None, NOW.replace(tzinfo=None), NOW + timedelta(hours=1), NOW.astimezone(timezone(timedelta(hours=5)))]
    items = [ActivityItem(id=UUID(int=i+1), vertical="news", title="Fixture", source_name="test", source_url="https://example.com", collected_at=value) for i, value in enumerate(dates)]
    ordered = sorted(items, key=activity_key, reverse=True)
    assert ordered[0].collected_at == NOW + timedelta(hours=1)
    assert ordered[-1].collected_at is None
    assert utc_timestamp(dates[1]) == utc_timestamp(dates[3]) == NOW


async def test_activity_handles_legacy_missing_timestamps():
    # Current collected_at columns disallow NULL. Mock legacy rows to verify
    # defensive API behavior without weakening or mutating the real schema.
    session = AsyncMock()
    session.get_bind = MagicMock()
    session.get_bind.return_value.dialect.name = "sqlite"
    result = MagicMock()
    result.all.return_value = [
        (uuid4(), "Unknown time", "test", "https://example.com/a", None),
        (uuid4(), "Naive UTC", "test", "https://example.com/b", NOW.replace(tzinfo=None)),
        (uuid4(), "Aware", "test", "https://example.com/c", NOW + timedelta(hours=1)),
    ]
    session.execute.return_value = result
    response = await recent_activity(limit=20, session=session)
    assert len(response.items) == 15
    assert response.items[0].title == "Aware"
    assert response.items[-1].collected_at is None
    assert session.execute.await_count == 5


# Task 3: exercise the entity explorer against stored database relationships.
@pytest.mark.parametrize("term", ["Displayed", " NORMALIZED ONLY ", "Needle", "needle alternate"])
async def test_entity_search_names_normalized_and_multiple_aliases(client, api_session, term):
    entity = CanonicalEntity(canonical_name="Displayed Corp", normalized_name="normalized only")
    api_session.add_all([entity, CanonicalEntity(canonical_name="Other", normalized_name="other")])
    await api_session.flush()
    for alias in ("Needle alias", "Needle alternate"):
        api_session.add(EntityAlias(canonical_entity_id=entity.id, alias=alias, normalized_alias=alias.lower()))
    await api_session.commit()
    result = (await client.get("/api/entities", params={"q": term, "limit": 1})).json()
    assert result["total"] == 1  # Two matching aliases must not duplicate an entity.
    assert result["has_more"] is False
    assert result["items"][0]["id"] == str(entity.id)
    assert result["items"][0]["aliases"] == ["Needle alias", "Needle alternate"]
    assert (await client.get("/api/entities", params={"q": term, "offset": 1})).json()["items"] == []
    assert (await client.get("/api/entities", params={"q": "   "})).json()["total"] == 2


@pytest.mark.parametrize("field", ["canonical_name", "normalized_name", "alias"])
@pytest.mark.parametrize("term", ["%", "_", "\\", "' OR 1=1 --"])
async def test_entity_search_treats_wildcards_and_sql_as_literal_text(client, api_session, field, term):
    entity = CanonicalEntity(canonical_name="Literal", normalized_name="literal")
    if field != "alias":
        setattr(entity, field, f"Literal {term} marker")
    api_session.add_all([entity, CanonicalEntity(canonical_name="Distractor", normalized_name="distractor")])
    await api_session.flush()
    if field == "alias":
        api_session.add(EntityAlias(canonical_entity_id=entity.id, alias=f"Literal {term} marker", normalized_alias="literal alias"))
    await api_session.commit()
    response = await client.get("/api/entities", params={"q": term})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [row["id"] for row in response.json()["items"]] == [str(entity.id)]
    assert (await client.get("/api/entities")).json()["total"] == 2


@pytest.mark.parametrize("sort,expected", [
    ("records", [4, 1, 2, 3]), ("name", [1, 2, 3, 4]),
    ("recent", [3, 1, 2, 4]), ("name; DROP TABLE canonical_entities", [4, 1, 2, 3]),
])
async def test_entity_sort_and_pagination_are_deterministic(client, api_session, sort, expected):
    # Reverse insertion order, equal names/timestamps/counts, unique normalized names.
    for index, name, age, links in [(4, "Zeta", -1, 2), (3, "Beta", 1, 0), (2, "Alpha", 0, 1), (1, "Alpha", 0, 1)]:
        entity = CanonicalEntity(id=UUID(int=index), canonical_name=name, normalized_name=f"entity {index}", created_at=NOW + timedelta(hours=age))
        api_session.add(entity)
        await api_session.flush()
        for link in range(links):
            api_session.add(Startup(entity_name=name, canonical_entity_id=entity.id, source_name="test", source_url=f"https://example.com/{index}/{link}"))
    await api_session.commit()
    params = {"sort": sort, "q": "entity"}
    whole = (await client.get("/api/entities", params=params)).json()
    assert [row["id"] for row in whole["items"]] == [str(UUID(int=i)) for i in expected]
    paged = []
    for offset in range(4):
        result = (await client.get("/api/entities", params={**params, "limit": 1, "offset": offset})).json()
        assert result["total"] == 4
        assert result["offset"] == offset and result["limit"] == 1
        assert result["has_more"] is (offset < 3)
        paged.extend(result["items"])
    assert paged == whole["items"]
    beyond = (await client.get("/api/entities", params={**params, "offset": 4})).json()
    assert beyond["total"] == 4 and beyond["items"] == [] and beyond["has_more"] is False


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": -1}, {"limit": 101}, {"offset": -1},
    {"limit": "invalid"}, {"q": "x" * 121},
])
async def test_entity_list_rejects_unsafe_pagination_and_search(client, params):
    assert (await client.get("/api/entities", params=params)).status_code == 422


async def test_entity_list_empty_database_and_unmatched_search(client, api_session):
    for params in ({}, {"q": "missing"}):
        result = (await client.get("/api/entities", params=params)).json()
        assert result["items"] == [] and result["total"] == 0 and result["has_more"] is False
    await _seed_entities(api_session)
    result = (await client.get("/api/entities?q=missing")).json()
    assert result["items"] == [] and result["total"] == 0 and result["has_more"] is False


async def test_entity_detail_twenty_per_type_exact_counts_and_no_inferred_links(client, api_session):
    target, other, empty = [CanonicalEntity(canonical_name="Same name", normalized_name=name, entity_type="company", created_at=NOW) for name in ("target", "other", "empty")]
    api_session.add_all([target, other, empty])
    await api_session.flush()
    unlinked = []
    expected = {}
    for model, kind, fields in (
        (Startup, "startups", {"entity_name": "Same name"}),
        (Product, "products", {"product_name": "Same name", "startup_name": "Same name"}),
        (Job, "jobs", {"title": "Same name", "company": "Same name"}),
    ):
        required = {"posted_at": NOW} if model is Job else {}
        rows = []
        for i in reversed(range(24)):
            # Relationships are FK-based even when the record name differs.
            row_fields = {key: f"Different recorded name {i}" for key in fields}
            url = {"url" if model is Job else "source_url": f"https://example.com/{kind}/{i}"}
            row = model(id=UUID(int=i+1), canonical_entity_id=target.id, source_name="test", collected_at=NOW + timedelta(hours=i % 2), **row_fields, **url, **required)
            api_session.add(row)
            rows.append(row)
        expected[kind] = [str(row.id) for row in sorted(rows, key=lambda row: (-row.collected_at.timestamp(), str(row.id)))][:20]
        # Identical names with no FK or with a different FK are not target links.
        for index, entity_id in enumerate((None, other.id)):
            url = {"url" if model is Job else "source_url": f"https://example.com/{kind}/distractor/{index}"}
            row = model(canonical_entity_id=entity_id, source_name="test", **fields, **url, **required)
            api_session.add(row)
            if entity_id is None:
                unlinked.append((kind, row))
    for i in reversed(range(25)):
        api_session.add(EntityAlias(canonical_entity_id=target.id, alias=f"Alias {i:02}", normalized_alias=f"alias {i:02}"))
    await api_session.commit()

    response = await client.get(f"/api/entities/{target.id}")
    assert response.status_code == 200
    detail = response.json()
    for key in ("id", "canonical_name", "normalized_name", "entity_type", "created_at"):
        assert detail[key]
    assert detail["canonical_name"] == "Same name" and detail["normalized_name"] == "target"
    assert detail["relationship_limit"] == 20
    assert detail["alias_count"] == 25
    assert detail["aliases"] == [f"Alias {i:02}" for i in range(20)]
    assert detail["startup_count"] == detail["product_count"] == detail["job_count"] == 24
    assert detail["total_records"] == 72
    for kind, ids in expected.items():
        assert [row["id"] for row in detail[kind]] == ids
        record = (await client.get(f"/api/{kind}/{ids[0]}")).json()
        assert record["canonical_entity_id"] == record["canonical_entity"]["id"] == str(target.id)
    assert (await client.get(f"/api/entities/{target.id}")).json() == detail
    # Searching an alias outside the preview must still find the stored entity.
    listed = (await client.get("/api/entities?q=Alias%2024")).json()["items"][0]
    for key in ("startup_count", "product_count", "job_count", "total_records", "aliases", "alias_count"):
        assert listed[key] == detail[key]
    for limit in (0, -1, 21, 50, 51):
        assert (await client.get(f"/api/entities/{target.id}", params={"relationship_limit": limit})).status_code == 422
    smaller = (await client.get(f"/api/entities/{target.id}?relationship_limit=1")).json()
    assert smaller["total_records"] == 72
    assert all(len(smaller[kind]) == 1 for kind in expected)
    orphan = (await client.get(f"/api/entities/{empty.id}")).json()
    assert orphan["total_records"] == orphan["startup_count"] == orphan["product_count"] == orphan["job_count"] == orphan["alias_count"] == 0
    assert orphan["aliases"] == orphan["startups"] == orphan["products"] == orphan["jobs"] == []
    for kind, row in unlinked:
        record = (await client.get(f"/api/{kind}/{row.id}")).json()
        assert record["canonical_entity_id"] is None and record["canonical_entity"] is None
