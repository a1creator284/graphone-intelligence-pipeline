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
