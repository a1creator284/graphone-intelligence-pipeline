"""
Focused API tests for the read-only dashboard backend.

Convention follows the rest of the suite: an in-memory SQLite database built
from the pipeline's own ``Base.metadata`` (see ``tests/conftest.py``), so
these tests never touch a live Postgres instance and never crawl anything.
The FastAPI ``get_session`` dependency is overridden to hand the app that
same session.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from dashboard.backend.app.config import MAX_PAGE_SIZE
from dashboard.backend.app.db import get_session
from dashboard.backend.app.main import create_app
from src.storage.database import build_engine
from src.storage.models import (
    Base,
    CanonicalEntity,
    Job,
    News,
    Product,
    RawDocument,
    ResearchPaper,
    Startup,
)

NOW = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def api_session():
    """Fresh in-memory DB carrying the real pipeline schema."""
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def client(api_session):
    """AsyncClient wired to the ASGI app with the DB dependency overridden."""
    app = create_app()

    async def _override():
        yield api_session

    app.dependency_overrides[get_session] = _override

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()


async def _seed(session, *, startups=1, products=1, papers=1, jobs=1, news=1, entities=1, raw=1):
    """Insert a deterministic, minimal fixture set across every table."""
    for i in range(startups):
        session.add(
            Startup(
                entity_name=f"Startup {i}",
                source_name="ycombinator",
                source_url=f"https://ycombinator.com/companies/{i}",
                employee_count=10 + i,
                collected_at=NOW - timedelta(minutes=i),
            )
        )
    for i in range(products):
        session.add(
            Product(
                product_name=f"Product {i}",
                startup_name=f"Vendor {i}",
                source_name="huggingface",
                source_url=f"https://huggingface.co/models/{i}",
                dedup_key=f"hf:{i}",
                pricing_model="FREE",
                collected_at=NOW - timedelta(minutes=i),
            )
        )
    for i in range(papers):
        session.add(
            ResearchPaper(
                title=f"Paper {i}",
                authors=["A. Author"],
                paper_url=f"https://arxiv.org/abs/{i}",
                github_stars=i,
                published_date=NOW - timedelta(days=i),
                source_name="arxiv",
                collected_at=NOW - timedelta(minutes=i),
            )
        )
    for i in range(jobs):
        session.add(
            Job(
                company=f"Company {i}",
                title=f"ML Engineer {i}",
                url=f"https://jobs.example.com/{i}",
                posted_at=NOW - timedelta(hours=i),
                is_remote=True,
                source_name="ycombinator_jobs",
                collected_at=NOW,
            )
        )
    for i in range(news):
        session.add(
            News(
                title=f"Headline {i}",
                url=f"https://news.example.com/{i}",
                source_name="hackernews_ai",
                published_at=NOW - timedelta(hours=i),
                collected_at=NOW,
            )
        )
    for i in range(entities):
        session.add(
            CanonicalEntity(canonical_name=f"Entity {i}", normalized_name=f"entity {i}")
        )
    for i in range(raw):
        session.add(
            RawDocument(
                source_name="ycombinator",
                source_url=f"https://ycombinator.com/raw/{i}",
                canonical_url=f"https://ycombinator.com/raw/{i}",
                content_hash=f"{i:064d}",
            )
        )
    await session.commit()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_reports_connected_database(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_returns_zeros_on_empty_database(client):
    resp = await client.get("/api/dashboard/stats")
    assert resp.status_code == 200
    stats = resp.json()["stats"]
    assert stats == {
        "startups": 0,
        "products": 0,
        "research_papers": 0,
        "jobs": 0,
        "news": 0,
        "canonical_entities": 0,
        "raw_documents": 0,
    }


@pytest.mark.asyncio
async def test_stats_counts_every_vertical(client, api_session):
    await _seed(api_session, startups=3, products=2, papers=4, jobs=5, news=6, entities=7, raw=8)

    resp = await client.get("/api/dashboard/stats")
    assert resp.status_code == 200
    body = resp.json()

    assert body["stats"] == {
        "startups": 3,
        "products": 2,
        "research_papers": 4,
        "jobs": 5,
        "news": 6,
        "canonical_entities": 7,
        "raw_documents": 8,
    }
    # The homepage renders this timestamp; it must always be present.
    assert body["generated_at"]


# ---------------------------------------------------------------------------
# List endpoints
# ---------------------------------------------------------------------------


LIST_ROUTES = [
    "/api/startups",
    "/api/products",
    "/api/research-papers",
    "/api/news",
    "/api/jobs",
]


@pytest.mark.parametrize("route", LIST_ROUTES)
@pytest.mark.asyncio
async def test_list_endpoints_return_empty_page_when_no_data(client, route):
    resp = await client.get(route)
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["has_more"] is False


@pytest.mark.parametrize("route", LIST_ROUTES)
@pytest.mark.asyncio
async def test_list_endpoints_return_seeded_rows(client, api_session, route):
    await _seed(api_session, startups=2, products=2, papers=2, jobs=2, news=2)

    resp = await client.get(route)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert all("id" in item for item in body["items"])


@pytest.mark.asyncio
async def test_startup_payload_exposes_expected_fields(client, api_session):
    await _seed(api_session, startups=1, products=0, papers=0, jobs=0, news=0, entities=0, raw=0)

    body = (await client.get("/api/startups")).json()
    item = body["items"][0]
    assert item["entity_name"] == "Startup 0"
    assert item["source_name"] == "ycombinator"
    assert item["employee_count"] == 10


@pytest.mark.asyncio
async def test_pagination_slices_results_and_reports_has_more(client, api_session):
    await _seed(api_session, startups=5, products=0, papers=0, jobs=0, news=0, entities=0, raw=0)

    first = (await client.get("/api/startups?limit=2&offset=0")).json()
    assert first["total"] == 5
    assert len(first["items"]) == 2
    assert first["has_more"] is True

    last = (await client.get("/api/startups?limit=2&offset=4")).json()
    assert len(last["items"]) == 1
    assert last["has_more"] is False

    # No overlap between pages -> ordering is stable enough to page through.
    assert first["items"][0]["id"] != last["items"][0]["id"]


@pytest.mark.asyncio
async def test_limit_above_cap_is_rejected_not_silently_clamped(client):
    """A caller must not be able to request an unbounded slice of a table."""
    resp = await client.get(f"/api/startups?limit={MAX_PAGE_SIZE + 1}")
    assert resp.status_code == 422


@pytest.mark.parametrize("query", ["limit=0", "limit=-1", "offset=-1"])
@pytest.mark.asyncio
async def test_invalid_pagination_params_are_rejected(client, query):
    resp = await client.get(f"/api/startups?{query}")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_news_is_ordered_newest_first(client, api_session):
    await _seed(api_session, startups=0, products=0, papers=0, jobs=0, news=3, entities=0, raw=0)

    items = (await client.get("/api/news")).json()["items"]
    published = [item["published_at"] for item in items]
    assert published == sorted(published, reverse=True)


# ---------------------------------------------------------------------------
# Read-only / CORS guarantees
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["/api/startups", "/api/dashboard/stats"])
@pytest.mark.asyncio
async def test_api_is_read_only(client, route):
    """No write verbs are exposed anywhere on the dashboard API."""
    assert (await client.post(route, json={})).status_code == 405
    assert (await client.delete(route)).status_code == 405


@pytest.mark.asyncio
async def test_cors_allows_local_frontend_origin_only(client):
    allowed = await client.get(
        "/api/health", headers={"Origin": "http://localhost:3000"}
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"

    # A foreign origin must not be echoed back, and "*" must never appear.
    foreign = await client.get(
        "/api/health", headers={"Origin": "https://evil.example.com"}
    )
    assert foreign.headers.get("access-control-allow-origin") not in {
        "*",
        "https://evil.example.com",
    }
