"""
Focused PRODUCTS vertical tests.

Scope is deliberately narrow -- CLI dispatch, the source registry, target
semantics under cross-source duplication, validation, and the repository's
unique constraints. The adapters' own HTTP/pagination behaviour is covered
by tests/test_products_adapters.py.

Every fixture payload here is shaped exactly like the real Hugging Face Hub
and OpenRouter responses (see the adapter module docstrings); nothing here
asserts a record count the mocked sources did not actually supply.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
import respx
from httpx import Response
from sqlalchemy import func, select

from src.config.sources import Vertical, get_sources_for_vertical
from src.pipeline.products import run_products_pipeline
from src.storage.models import Product
from src.validation.schemas import validate_product_record

HF_SPACES_RE = r"https://huggingface\.co/api/spaces.*"
HF_MODELS_RE = r"https://huggingface\.co/api/models.*"
OPENROUTER_RE = r"https://openrouter\.ai/api/v1/models.*"


# --------------------------------------------------------------------------
# payload builders (shaped like the real APIs)
# --------------------------------------------------------------------------


def hf_space(owner: str, repo: str, *, title: str | None = None) -> dict:
    item = {"id": f"{owner}/{repo}", "author": owner, "likes": 3, "sdk": "gradio"}
    if title is not None:
        item["cardData"] = {"title": title}
    return item


def hf_model(owner: str, repo: str) -> dict:
    return {"id": f"{owner}/{repo}", "author": owner, "downloads": 100}


def or_model(model_id: str, name: str, *, hugging_face_id: str | None = None) -> dict:
    item = {
        "id": model_id,
        "name": name,
        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
    }
    if hugging_face_id is not None:
        item["hugging_face_id"] = hugging_face_id
    return item


def mock_products_sources(
    router: respx.MockRouter,
    *,
    spaces: list[dict],
    openrouter: list[dict],
    models: list[dict],
) -> None:
    """Serve each source's full listing on every request.

    No `Link: rel="next"` header and no growing offset window, so each source
    is a fixed, finite population -- exactly the condition under which a
    padding implementation would be caught inventing records.
    """
    router.get(url__regex=HF_SPACES_RE).mock(
        return_value=Response(200, text=json.dumps(spaces))
    )
    router.get(url__regex=HF_MODELS_RE).mock(
        return_value=Response(200, text=json.dumps(models))
    )
    router.get(url__regex=OPENROUTER_RE).mock(
        return_value=Response(200, text=json.dumps({"data": openrouter}))
    )


# --------------------------------------------------------------------------
# 1. source registry
# --------------------------------------------------------------------------


def test_registry_enables_exactly_the_three_products_sources():
    names = [s.name for s in get_sources_for_vertical(Vertical.PRODUCTS)]
    assert names == ["huggingface_spaces", "openrouter_models", "huggingface_models"]


def test_registry_names_match_the_adapter_class_names():
    """A registry entry that does not match its adapter is a silent lie about
    what the pipeline runs, so pin the two together."""
    from src.pipeline.products import ADAPTER_CLASSES

    registered = [s.name for s in get_sources_for_vertical(Vertical.PRODUCTS)]
    assert registered == [cls.name for cls in ADAPTER_CLASSES]


def test_producthunt_stays_disabled_without_an_adapter_or_credentials():
    """Product Hunt needs an OAuth token and has no adapter in src/crawlers/,
    so enabling it would advertise a capability that cannot run."""
    from src.config.sources import SOURCE_REGISTRY

    entry = next(s for s in SOURCE_REGISTRY if s.name == "producthunt_ai_products")
    assert entry.enabled is False
    assert entry.name not in [s.name for s in get_sources_for_vertical(Vertical.PRODUCTS)]


def test_every_enabled_products_source_is_importable_and_public():
    """Each enabled source must resolve to a real adapter whose vertical is
    products -- no placeholder entries."""
    from src.pipeline.products import ADAPTER_CLASSES

    by_name = {cls.name: cls for cls in ADAPTER_CLASSES}
    for source in get_sources_for_vertical(Vertical.PRODUCTS):
        adapter_cls = by_name[source.name]
        assert adapter_cls.vertical == "products"
        assert str(source.base_url).startswith("https://")


# --------------------------------------------------------------------------
# 2. CLI dispatch
# --------------------------------------------------------------------------


class _DummyEngine:
    def begin(self):
        class _Ctx:
            async def __aenter__(self):
                class _Conn:
                    async def run_sync(self, func):
                        pass

                return _Conn()

            async def __aexit__(self, *exc):
                pass

        return _Ctx()


@asynccontextmanager
async def _dummy_engine_scope(database_url=None):
    yield _DummyEngine()


def _dummy_factory(session):
    class _Factory:
        def __call__(self):
            class _Ctx:
                async def __aenter__(self):
                    return session

                async def __aexit__(self, *exc):
                    pass

            return _Ctx()

    return _Factory()


@pytest.mark.asyncio
async def test_cli_products_vertical_is_wired(capsys, db_session):
    """`--vertical products --target 5 --workers 7` must execute the products
    pipeline (not print `vertical_not_yet_wired`), forwarding --target and
    --workers through exactly like the other verticals."""
    from src.main import _run, build_parser
    from src.pipeline.products import ProductsPipelineResult

    called: dict[str, object] = {}

    async def fake_pipeline(session, *, target=1000, max_concurrency=20, **kwargs):
        called["session"] = session
        called["target"] = target
        called["max_concurrency"] = max_concurrency
        return ProductsPipelineResult(
            target=target,
            discovered=1,
            valid_records=target,
            by_source={"huggingface_spaces": target},
        )

    with patch("sys.argv", ["python -m src.main", "--vertical", "products", "--target", "5", "--workers", "7"]), \
            patch("src.pipeline.products.run_products_pipeline", side_effect=fake_pipeline), \
            patch("src.main.engine_scope", side_effect=_dummy_engine_scope), \
            patch("src.storage.database.get_session_factory", return_value=_dummy_factory(db_session)):
        args = build_parser().parse_args()
        exit_code = await _run(args)

    assert exit_code == 0
    assert called["target"] == 5
    assert called["max_concurrency"] == 7
    assert called["session"] is db_session

    stdout = capsys.readouterr().out
    assert "vertical_not_yet_wired" not in stdout
    assert "products_run_summary" in stdout


@pytest.mark.asyncio
async def test_cli_products_dry_run_reports_sources_without_running(capsys):
    """`--dry-run` reports the registered products sources without invoking
    the pipeline or touching the database."""
    from unittest.mock import AsyncMock

    from src.main import _run, build_parser

    with patch("sys.argv", ["python -m src.main", "--vertical", "products", "--dry-run"]), \
            patch("src.pipeline.products.run_products_pipeline", new_callable=AsyncMock) as mock_pipeline:
        args = build_parser().parse_args()
        exit_code = await _run(args)

    assert exit_code == 0
    mock_pipeline.assert_not_awaited()

    stdout = capsys.readouterr().out
    assert "huggingface_spaces" in stdout
    assert "openrouter_models" in stdout
    assert "huggingface_models" in stdout
    assert "products_run_summary" not in stdout


# --------------------------------------------------------------------------
# 3. target semantics
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_cross_source_duplicates_do_not_block_reaching_target(db_session):
    """The regression this guards against.

    openrouter_models declares `hugging_face_id` values that collide with
    huggingface_models records, so a naive single-pass allocation stops short
    of the target even though the later source still has unread real records.
    The target must still be met -- from genuinely distinct records.
    """
    spaces = [hf_space("acme", f"space-{i}") for i in range(4)]
    # Every OpenRouter model points at an HF repo that huggingface_models
    # also lists, so the two sources overlap completely on dedup_key.
    shared = [f"labs/model-{i}" for i in range(4)]
    openrouter = [
        or_model(f"labs/model-{i}", f"Labs: Model {i}", hugging_face_id=shared[i])
        for i in range(4)
    ]
    # The HF models listing carries those same repos *plus* extra real ones.
    models = [hf_model("labs", f"model-{i}") for i in range(4)] + [
        hf_model("labs", f"extra-{i}") for i in range(6)
    ]

    mock_products_sources(respx, spaces=spaces, openrouter=openrouter, models=models)

    result = await run_products_pipeline(db_session, target=12, max_concurrency=4)

    # 4 spaces + 4 openrouter + 4 non-colliding hf models = 12 real records.
    assert result.valid_records == 12
    assert result.cross_source_duplicates > 0

    rows = (await db_session.execute(select(Product))).scalars().all()
    assert len(rows) == 12
    # Every persisted row is distinct by URL and by dedup_key.
    assert len({r.source_url for r in rows}) == 12
    assert len({r.dedup_key for r in rows}) == 12


@pytest.mark.asyncio
@respx.mock
async def test_target_shortfall_is_reported_not_padded(db_session):
    """When the sources genuinely cannot supply the target, the pipeline
    reports the real count. No row is invented, duplicated, or padded."""
    spaces = [hf_space("acme", "only-space")]
    openrouter = [or_model("labs/only-model", "Labs: Only Model")]
    models = [hf_model("labs", "only-hf-model")]

    mock_products_sources(respx, spaces=spaces, openrouter=openrouter, models=models)

    result = await run_products_pipeline(db_session, target=500, max_concurrency=4)

    assert result.valid_records == 3
    assert result.valid_records < result.target

    rows = (await db_session.execute(select(Product))).scalars().all()
    assert len(rows) == 3
    # Every stored row traces back to a URL the mocked sources actually served.
    assert {r.source_url for r in rows} == {
        "https://huggingface.co/spaces/acme/only-space",
        "https://openrouter.ai/labs/only-model",
        "https://huggingface.co/labs/only-hf-model",
    }


@pytest.mark.asyncio
@respx.mock
async def test_target_is_a_ceiling_not_a_quota(db_session):
    """More real records available than requested -> stop at the target."""
    spaces = [hf_space("acme", f"space-{i}") for i in range(20)]
    openrouter = [or_model(f"labs/m-{i}", f"Labs: M {i}") for i in range(20)]
    models = [hf_model("labs", f"hf-{i}") for i in range(20)]

    mock_products_sources(respx, spaces=spaces, openrouter=openrouter, models=models)

    result = await run_products_pipeline(db_session, target=6, max_concurrency=4)

    assert result.valid_records == 6
    count = (await db_session.execute(select(func.count()).select_from(Product))).scalar_one()
    assert count == 6


@pytest.mark.asyncio
@respx.mock
async def test_source_order_is_deterministic(db_session):
    """Fixed source order => the same run twice yields the same rows."""
    spaces = [hf_space("acme", f"space-{i}") for i in range(5)]
    openrouter = [or_model(f"labs/m-{i}", f"Labs: M {i}") for i in range(5)]
    models = [hf_model("labs", f"hf-{i}") for i in range(5)]

    mock_products_sources(respx, spaces=spaces, openrouter=openrouter, models=models)

    first = await run_products_pipeline(db_session, target=6, max_concurrency=4)
    urls_first = {
        r.source_url for r in (await db_session.execute(select(Product))).scalars().all()
    }

    # A second identical run must not add anything (DB constraints hold) and
    # must have selected the same records.
    second = await run_products_pipeline(db_session, target=6, max_concurrency=4)
    urls_second = {
        r.source_url for r in (await db_session.execute(select(Product))).scalars().all()
    }

    assert first.valid_records == 6
    assert second.valid_records == 0  # all duplicates of the first run
    assert urls_first == urls_second


@pytest.mark.asyncio
@respx.mock
async def test_similar_names_from_different_urls_stay_separate(db_session):
    """Identity is the source URL / source-derived dedup_key, never the name.

    Two vendors publishing a product with the *same* display name are two
    real products and must both survive.
    """
    spaces = [
        hf_space("acme", "chatbot", title="ChatBot"),
        hf_space("globex", "chatbot", title="ChatBot"),
        hf_space("initech", "chat-bot", title="ChatBot"),
    ]
    mock_products_sources(respx, spaces=spaces, openrouter=[], models=[])

    result = await run_products_pipeline(db_session, target=3, max_concurrency=4)

    assert result.valid_records == 3
    rows = (await db_session.execute(select(Product))).scalars().all()
    assert len(rows) == 3
    # Same product_name, different vendors and URLs -- all three kept.
    assert {r.product_name for r in rows} == {"ChatBot"}
    assert {r.startup_name for r in rows} == {"acme", "globex", "initech"}
    assert len({r.source_url for r in rows}) == 3


@pytest.mark.asyncio
@respx.mock
async def test_provenance_and_source_url_come_from_the_source(db_session):
    """source_url must be the source's canonical URL and every row must keep
    its raw provenance document."""
    from src.storage.models import RawDocument

    spaces = [hf_space("acme", "space-a"), hf_space("acme", "space-b")]
    mock_products_sources(respx, spaces=spaces, openrouter=[], models=[])

    await run_products_pipeline(db_session, target=2, max_concurrency=4)

    rows = (await db_session.execute(select(Product))).scalars().all()
    assert rows
    for row in rows:
        assert row.raw_document_id is not None
        assert row.source_name == "huggingface_spaces"
        assert row.source_url.startswith("https://huggingface.co/spaces/")
        assert row.source_external_id in {"acme/space-a", "acme/space-b"}
        # The Hub publishes no price for Spaces -- it must not be guessed.
        assert row.pricing_model is None

    raw_count = (await db_session.execute(select(func.count()).select_from(RawDocument))).scalar_one()
    assert raw_count >= 1


@pytest.mark.asyncio
@respx.mock
async def test_records_missing_identity_fields_are_skipped_not_invented(db_session):
    """Items with no usable owner are dropped rather than attributed to a
    fabricated vendor."""
    spaces = [
        {"id": "nameless", "likes": 1},  # no owner segment, no author
        hf_space("acme", "real-space"),
    ]
    mock_products_sources(respx, spaces=spaces, openrouter=[], models=[])

    result = await run_products_pipeline(db_session, target=5, max_concurrency=4)

    assert result.valid_records == 1
    rows = (await db_session.execute(select(Product))).scalars().all()
    assert [r.source_url for r in rows] == ["https://huggingface.co/spaces/acme/real-space"]


# --------------------------------------------------------------------------
# 4. ProductRecord validation
# --------------------------------------------------------------------------


def _valid_payload(**overrides) -> dict:
    payload = {
        "product_name": "ChatBot",
        "startup_name": "acme",
        "source_url": "https://huggingface.co/spaces/acme/chatbot",
        "source_name": "huggingface_spaces",
        "source_external_id": "acme/chatbot",
        "dedup_key": "hf-space:acme/chatbot",
        "pricing_model": None,
        "metadata_json": {"likes": 3},
    }
    payload.update(overrides)
    return payload


def test_product_record_accepts_a_real_source_payload():
    record, error = validate_product_record(_valid_payload())
    assert error is None
    assert record is not None
    assert record.record_type == "PRODUCT"
    assert record.startup_name == "acme"
    assert record.pricing_model is None


def test_product_record_rejects_blank_vendor():
    record, error = validate_product_record(_valid_payload(startup_name="   "))
    assert record is None
    assert error is not None


def test_product_record_rejects_non_http_source_url():
    record, error = validate_product_record(_valid_payload(source_url="not-a-url"))
    assert record is None
    assert error is not None


def test_product_record_blank_product_name_becomes_null_not_empty_string():
    record, _ = validate_product_record(_valid_payload(product_name="  "))
    assert record is not None
    assert record.product_name is None


def test_product_record_rejects_unknown_pricing_model():
    record, error = validate_product_record(_valid_payload(pricing_model="CHEAP"))
    assert record is None
    assert error is not None


def test_product_record_normalizes_known_pricing_model():
    record, _ = validate_product_record(_valid_payload(pricing_model="paid"))
    assert record is not None
    assert record.pricing_model == "PAID"


def test_product_record_allows_null_optional_fields():
    """A source that publishes no title / external id / key is still a valid
    record -- the missing values stay null instead of being filled in."""
    record, error = validate_product_record(
        _valid_payload(product_name=None, source_external_id=None, dedup_key=None)
    )
    assert error is None
    assert record is not None
    assert record.product_name is None
    assert record.dedup_key is None


# --------------------------------------------------------------------------
# 5. ProductRepository unique constraints
# --------------------------------------------------------------------------


def _row(**overrides) -> dict:
    row = {
        "product_name": "ChatBot",
        "startup_name": "acme",
        "source_name": "huggingface_spaces",
        "source_url": "https://huggingface.co/spaces/acme/chatbot",
        "source_external_id": "acme/chatbot",
        "dedup_key": "hf-space:acme/chatbot",
        "metadata_json": {},
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_product_repository_second_insert_of_same_url_is_a_no_op(db_session):
    from src.storage.repositories import ProductRepository

    repo = ProductRepository(db_session)
    assert await repo.upsert(**_row()) is True
    # Same (source_name, source_url), different dedup_key -> still one row.
    assert await repo.upsert(**_row(dedup_key="hf-space:other")) is False

    count = (await db_session.execute(select(func.count()).select_from(Product))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_product_repository_collapses_same_dedup_key_across_sources(db_session):
    """The cross-source artifact key. Same real-world model listed twice ->
    one row, no IntegrityError."""
    from src.storage.repositories import ProductRepository

    repo = ProductRepository(db_session)
    assert await repo.upsert(
        **_row(
            source_name="openrouter_models",
            source_url="https://openrouter.ai/labs/model-a",
            dedup_key="hf-model:labs/model-a",
        )
    ) is True
    assert await repo.upsert(
        **_row(
            source_name="huggingface_models",
            source_url="https://huggingface.co/labs/model-a",
            dedup_key="hf-model:labs/model-a",
        )
    ) is False

    count = (await db_session.execute(select(func.count()).select_from(Product))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_product_repository_keeps_similar_names_with_distinct_urls(db_session):
    """A name collision must never drop a genuinely different product."""
    from src.storage.repositories import ProductRepository

    repo = ProductRepository(db_session)
    assert await repo.upsert(
        **_row(source_url="https://huggingface.co/spaces/acme/chatbot", dedup_key="hf-space:acme/chatbot")
    ) is True
    assert await repo.upsert(
        **_row(
            startup_name="globex",
            source_url="https://huggingface.co/spaces/globex/chatbot",
            dedup_key="hf-space:globex/chatbot",
        )
    ) is True

    rows = (await db_session.execute(select(Product))).scalars().all()
    assert len(rows) == 2
    assert {r.product_name for r in rows} == {"ChatBot"}


@pytest.mark.asyncio
async def test_product_repository_allows_multiple_null_dedup_keys(db_session):
    """A NULL dedup_key is 'unknown', not a value, so it must not collide --
    otherwise only the first key-less product from any source would survive."""
    from src.storage.repositories import ProductRepository

    repo = ProductRepository(db_session)
    assert await repo.upsert(
        **_row(source_url="https://huggingface.co/spaces/acme/one", dedup_key=None)
    ) is True
    assert await repo.upsert(
        **_row(source_url="https://huggingface.co/spaces/acme/two", dedup_key=None)
    ) is True

    count = (await db_session.execute(select(func.count()).select_from(Product))).scalar_one()
    assert count == 2


@pytest.mark.asyncio
async def test_product_repository_exists_reflects_persisted_rows(db_session):
    from src.storage.repositories import ProductRepository

    repo = ProductRepository(db_session)
    url = "https://huggingface.co/spaces/acme/chatbot"
    assert await repo.exists("huggingface_spaces", url) is False
    await repo.upsert(**_row(source_url=url))
    assert await repo.exists("huggingface_spaces", url) is True
