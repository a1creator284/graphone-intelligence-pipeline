"""Opt-in browser regressions against a running production frontend.

DASHBOARD_TEST_URL=http://localhost:3000 python -m pytest dashboard/frontend/test_browser.py
Requires the repository's Playwright dependency and its Chromium installation.
All fixture responses are intercepted inside an isolated browser context; this
module never seeds, writes to, or changes the dashboard's real database.
"""
import json
import os
from urllib.parse import parse_qs, urlparse

import pytest

BASE_URL = os.environ.get("DASHBOARD_TEST_URL")
pytestmark = pytest.mark.skipif(not BASE_URL, reason="Set DASHBOARD_TEST_URL to opt into browser checks")
ROUTES = {"startups": "startups", "products": "products", "research": "research-papers", "news": "news", "jobs": "jobs", "entities": "entities"}
STAMP = "2026-09-11T12:00:00"  # Pipeline-naive timestamps are UTC.
ENTITY = {"id": "entity-1", "canonical_name": "Fixture identity", "normalized_name": "fixture identity", "entity_type": "company", "created_at": STAMP}
COMMON = {"source_name": "browser-test-only", "collected_at": STAMP, "canonical_entity_id": "entity-1", "canonical_entity": ENTITY, "raw_document_id": "raw-1", "provenance": {"id": "raw-1", "source_name": "browser-test-only", "source_url": "https://example.com/original", "canonical_url": "javascript:alert('blocked')", "retrieved_at": STAMP, "http_status": 200, "content_hash": "fixture-hash", "extraction_status": "extracted"}}
RECORDS = {
    "startups": {**COMMON, "id": "startup-1", "entity_name": "Fixture startup", "source_url": "https://example.com/startup", "employee_count": 0},
    "products": {**COMMON, "id": "product-1", "product_name": "Fixture product", "startup_name": "Fixture vendor", "source_url": "https://example.com/product", "pricing_model": "free", "source_external_id": "external-1", "metadata_json": {"test": "<script>not executable</script>"}},
    "research-papers": {**COMMON, "id": "paper-1", "title": "Fixture research", "authors": ["Author A", {"name": "Author B"}], "paper_url": "https://example.com/paper", "github_url": "data:text/html,unsafe", "github_stars": 0, "published_date": None, "paper_external_id": "paper-external"},
    "news": {**COMMON, "id": "news-1", "title": "Fixture news", "url": "https://example.com/news", "published_at": None, "extracted_metadata": {"test": True}},
    "jobs": {**COMMON, "id": "job-1", "title": "Fixture job", "company": "Fixture employer", "url": "https://example.com/job", "posted_at": None, "is_remote": None, "role_family": None},
}
RECORDS["entities"] = {**ENTITY, "alias_count": 25, "aliases": ["Fixture alias"], "startup_count": 26, "product_count": 1, "job_count": 0, "total_records": 27, "relationship_limit": 20, "startups": [RECORDS["startups"]], "products": [RECORDS["products"]], "jobs": []}


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        yield instance
        instance.close()


@pytest.fixture
def ui(browser):
    context = browser.new_context(timezone_id="America/Los_Angeles", viewport={"width": 1280, "height": 900})
    page = context.new_page()
    state = {"activity": "data", "detail_status": 200, "hold": False, "pending": [], "requests": [], "errors": []}
    page.on("pageerror", lambda error: state["errors"].append(str(error)))

    def respond(route):
        parsed = urlparse(route.request.url)
        path = parsed.path.removeprefix("/api/")
        query = parse_qs(parsed.query)
        state["requests"].append((path, query))
        status = 200
        if path == "health":
            data = {"status": "ok", "database": "connected"}
        elif path == "dashboard/stats":
            data = {"stats": {key: 1 for key in ["startups", "products", "research_papers", "jobs", "news", "canonical_entities", "raw_documents"]}, "generated_at": STAMP + "Z"}
        elif path == "dashboard/recent-activity":
            if state["activity"] == "hold":
                state["pending"].append(route)
                return
            status = 503 if state["activity"] == "error" else 200
            data = {"limit": 10, "items": [] if state["activity"] != "data" else [{"id": "startup-1", "vertical": "startups", "title": "Fixture activity", "source_name": "browser-test-only", "source_url": "https://example.com/activity", "collected_at": None}]}
        elif "/" in path:
            if state["hold"]:
                state["pending"].append(route)
                return
            status = state["detail_status"]
            data = RECORDS[path.split("/")[0]]
        else:
            row = RECORDS[path]
            offset = int(query.get("offset", [0])[0])
            limit = int(query.get("limit", [25])[0])
            total = 26
            items = [dict(row, id=f"{row['id']}-{n}") for n in range(offset, min(total, offset + limit))]
            if query.get("q") == ["no-matches"]:
                items, total = [], 0
            data = {"items": items, "total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total}
        route.fulfill(status=status, content_type="application/json", headers={"access-control-allow-origin": "*"}, body=json.dumps(data))

    context.route("**/api/**", respond)
    yield page, state
    assert state["errors"] == []
    context.close()


@pytest.mark.parametrize("route,kind", ROUTES.items())
def test_routes_details_keyboard_and_safe_links(ui, route, kind):
    from playwright.sync_api import expect
    page, state = ui
    assert page.goto(f"{BASE_URL}/{route}").status == 200
    trigger = page.locator(".row-detail-button").first
    expect(trigger).to_be_visible()
    trigger.focus()
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("heading", name="Intelligence detail")).not_to_be_visible()
    expect(dialog.locator("h2")).to_contain_text("Fixture")
    assert any(path.startswith(kind + "/") for path, _ in state["requests"])
    assert dialog.locator('a[href^="javascript:"], a[href^="data:"]').count() == 0
    for link in dialog.locator("a").all():
        assert link.get_attribute("target") == "_blank"
        assert link.get_attribute("rel") == "noopener noreferrer"
    if kind != "entities":
        expect(dialog.get_by_role("heading", name="Source & provenance")).to_be_visible()
        expect(dialog.get_by_text("fixture-hash", exact=True)).to_be_visible()
        # Los Angeles is UTC-7 in September: naive noon must display as 5am.
        expect(dialog.locator(".detail-field").filter(has=page.get_by_text("Collected", exact=True))).to_contain_text("5:00:00 AM")
    if kind == "jobs":
        expect(dialog.locator(".detail-field").filter(has=page.get_by_text("Work arrangement", exact=True))).to_contain_text("Not recorded")
    for _ in range(12):
        page.keyboard.press("Tab")
        assert page.evaluate("document.querySelector('dialog').contains(document.activeElement)")
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()
    # Clicking ordinary row content is also supported.
    page.locator("tbody tr").first.locator("td").nth(1).click()
    expect(dialog).to_be_visible()
    page.get_by_role("button", name="Close details").click()
    expect(trigger).to_be_focused()


def test_entity_navigation_bounds_and_back(ui):
    from playwright.sync_api import expect
    page, _ = ui
    page.goto(f"{BASE_URL}/startups")
    page.locator(".row-detail-button").first.click()
    page.get_by_role("button", name="View entity").click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_role("heading", name="Fixture identity", exact=True)).to_be_visible()
    expect(dialog.get_by_text("Showing 1 of 25 aliases", exact=False)).to_be_visible()
    expect(dialog.get_by_text("1 of 26", exact=False)).to_be_visible()
    expect(dialog.get_by_text("No linked jobs.", exact=True)).to_be_visible()
    dialog.get_by_role("button", name="Fixture product").click()
    expect(dialog.get_by_role("heading", name="Fixture product", exact=True)).to_be_visible()
    dialog.get_by_role("button", name="Back").click()
    expect(dialog.get_by_role("heading", name="Fixture identity", exact=True)).to_be_visible()
    dialog.get_by_role("button", name="Back").click()
    expect(dialog.get_by_role("heading", name="Fixture startup", exact=True)).to_be_visible()


def test_list_controls_survive_details(ui):
    from playwright.sync_api import expect
    page, state = ui
    page.goto(f"{BASE_URL}/startups")
    page.get_by_role("searchbox").fill("fixture")
    page.get_by_label("Sort records").select_option("name")
    expect(page.locator(".row-detail-button")).to_have_count(25)
    page.get_by_role("button", name="Next page").click()
    expect(page.locator(".row-detail-button")).to_have_count(1)
    page.locator(".row-detail-button").click()
    page.get_by_role("button", name="Close details").click()
    expect(page.get_by_role("searchbox")).to_have_value("fixture")
    expect(page.get_by_label("Sort records")).to_have_value("name")
    expect(page.locator(".row-detail-button")).to_have_count(1)
    assert any(q.get("offset") == ["25"] and q.get("sort") == ["name"] and q.get("q") == ["fixture"] for _, q in state["requests"])
    page.get_by_role("searchbox").fill("no-matches")
    expect(page.get_by_text("No matches", exact=True)).to_be_visible()
    page.get_by_role("button", name="Clear search", exact=True).click()
    expect(page.locator(".row-detail-button")).to_have_count(25)


def test_details_404_retry_and_cancelled_request(ui):
    from playwright.sync_api import expect
    page, state = ui
    state["detail_status"] = 404
    page.goto(f"{BASE_URL}/products")
    page.locator(".row-detail-button").first.click()
    expect(page.get_by_text("Record not found", exact=True)).to_be_visible()
    state["detail_status"] = 200
    page.get_by_role("button", name="Retry details").click()
    expect(page.get_by_role("heading", name="Fixture product", exact=True)).to_be_visible()
    page.get_by_role("button", name="Close details").click()
    state["hold"] = True
    page.locator(".row-detail-button").first.click()
    expect(page.get_by_text("Loading record details…", exact=True)).to_be_visible()
    page.get_by_role("button", name="Close details").click()
    state["hold"] = False
    page.locator(".row-detail-button").nth(1).click()
    expect(page.get_by_role("heading", name="Fixture product", exact=True)).to_be_visible()
    for pending in state["pending"]:
        pending.abort()
    expect(page.get_by_role("alert")).to_have_count(0)


def test_activity_empty_error_loading_refresh_and_details(ui):
    from playwright.sync_api import expect
    page, state = ui
    state["activity"] = "empty"
    assert page.goto(BASE_URL).status == 200
    expect(page.get_by_text("Backend connected", exact=True)).to_be_visible()
    expect(page.get_by_text("No activity yet", exact=True)).to_be_visible()
    expect(page.locator(".activity-item")).to_have_count(0)
    state["activity"] = "error"
    page.get_by_role("button", name="Refresh activity").click()
    expect(page.get_by_text("Could not load recent activity", exact=True)).to_be_visible()
    state["activity"] = "hold"
    page.get_by_role("button", name="Retry activity").click()
    expect(page.get_by_text("Loading recent activity…", exact=True)).to_be_visible()
    state["activity"] = "data"
    for pending in state["pending"]:
        pending.fulfill(status=200, content_type="application/json", headers={"access-control-allow-origin": "*"}, body=json.dumps({"items": [], "limit": 10}))
    expect(page.get_by_text("No activity yet", exact=True)).to_be_visible()
    page.get_by_role("button", name="Refresh activity").click()
    expect(page.locator(".activity-item")).to_have_count(1)
    expect(page.get_by_text("Collection time unavailable", exact=True)).to_be_visible()
    page.get_by_role("button", name="Fixture activity", exact=True).click()
    expect(page.get_by_role("heading", name="Fixture startup", exact=True)).to_be_visible()


def test_mobile_drawer_and_backdrop(ui):
    from playwright.sync_api import expect
    page, _ = ui
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{BASE_URL}/entities")
    page.locator(".row-detail-button").first.click()
    expect(page.get_by_role("heading", name="Fixture identity", exact=True)).to_be_visible()
    assert page.evaluate("document.querySelector('dialog').scrollWidth <= window.innerWidth")
    page.get_by_role("button", name="Close details").click()
    page.set_viewport_size({"width": 1280, "height": 900})
    page.locator(".row-detail-button").first.click()
    page.mouse.click(20, 400)
    expect(page.get_by_role("dialog")).not_to_be_visible()
