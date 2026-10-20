"""Read-only live smoke plus deterministic worksheet failure/interaction checks."""
import json
import os
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("NBAI_BASE_URL", "http://127.0.0.1:8770")
OUT = ROOT / "data/features/manual_orders/20261020-v1/ui"
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--host-resolver-rules=MAP nbai.space 216.198.79.65"] if "nbai.space" in BASE else [])
    context = browser.new_context()
    context.route("https://kalshi.com/**", lambda r: r.fulfill(body="<title>Kalshi destination test</title>", content_type="text/html"))
    page = context.new_page()
    errors, writes = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: writes.append(r.url) if r.method not in ("GET", "HEAD") else None)
    page.set_viewport_size(dict(width=1440, height=1100))
    page.goto(BASE + "/trading.html")
    page.wait_for_function("document.querySelector('#feedStatus').textContent.includes('current quotes')", timeout=40000)
    assert page.locator("#watchRows .watch-row").count() == 12
    assert page.locator("#shortlist .order-card").count() == 3, page.locator("#watchRows").inner_text()
    assert page.locator("#readyCount").inner_text() == "12"
    page.screenshot(path=str(OUT / "desktop.png"), full_page=True)
    for width in (390, 768):
        page.set_viewport_size(dict(width=width, height=900))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(OUT / f"mobile-{width}.png"), full_page=True)
    fields = page.locator("#shortlist .entry-fields").first
    assert fields.locator(".ticket-market").inner_text() == "Payton Pritchard: 15+ points"
    assert "Shares" in fields.inner_text() and "22" in fields.inner_text()
    assert "42 in a ¢ field" in fields.inner_text()
    assert "Submit as resting order only" in fields.inner_text()
    assert "Leave unchecked" in fields.inner_text()
    assert "Expiration" in fields.inner_text() and "Custom" in fields.inner_text()
    assert page.locator(".handoff-dialog").count() == 0
    assert "Copy details" not in page.locator("main").inner_text()
    with page.expect_popup() as popup_info:
        fields.locator("a.kalshi-link").click()
    popup = popup_info.value
    popup.wait_for_url("https://kalshi.com/")
    assert popup.evaluate("window.opener === null")
    popup.close()
    page.bring_to_front()
    page.wait_for_selector("#shortlist .entry-fields", timeout=40000)
    before = page.locator("#shortlist .ticket-fields").first.inner_text()
    page.locator("#orderBudget").fill("20")
    assert page.locator("#shortlist .ticket-fields").first.inner_text() != before
    page.locator("#orderBudget").fill("10")
    # The Detroit-win worksheet uses Boston / NO. Never imply NO on Detroit.
    assert 'BUY NO on “Boston wins”' in page.locator("#watchRows .watch-row").first.inner_text()
    assert "below current ask" in page.locator("#watchRows .watch-row").first.inner_text()
    page.locator("#watchRows .watch-row button").first.click()
    det = page.locator("#watchRows .watch-entry").first.locator(".entry-fields")
    assert det.is_visible()
    assert det.locator(".ticket-market").inner_text() == "Boston wins"
    assert 'On a “Detroit wins” ticket, YES means Detroit wins' in det.inner_text()
    assert 'Boston wins” / NO' in det.inner_text()
    assert "Shares" in det.inner_text() and "46¢" in det.inner_text()
    page.locator("#orderBudget").fill("0")
    assert page.locator("#watchRows button:enabled").count() == 0
    page.locator("#orderBudget").fill("10")
    page.locator("#combosTab").click()
    assert page.locator("#watchRows .combo-row").count() == 9
    assert page.locator("#watchRows button").count() == 0
    page.locator("#gameFilter").select_option("0022600003")
    assert page.locator("#watchRows .combo-row").count() == 3
    page.locator("#singlesTab").click()
    assert page.locator("#watchRows .watch-row").count() == 4
    page.locator("#gameFilter").select_option("all")

    # Fixture requests provide timestamped observations without contacting the exchange.
    watch = json.loads((ROOT / "web/game-watchlist.json").read_text())
    models = json.loads((ROOT / "web/player-game-projections.json").read_text())
    availability = json.loads((ROOT / "web/game-availability.json").read_text())
    reference = json.loads((ROOT / "web/manual-order-snapshot.json").read_text())
    live = {q["gameId"]: deepcopy(q) for q in reference["quotes"]}
    mode = {"value": "normal"}

    def quote_route(route):
        game = parse_qs(urlparse(route.request.url).query)["game"][0]
        response = deepcopy(live[game])
        if mode["value"] == "failed" and game == "0022600001":
            route.fulfill(status=503, json={"error": "Unavailable", "quotes": []})
            return
        for quote in response["quotes"]:
            quote["receivedAt"] = (datetime.now(timezone.utc) - timedelta(seconds=90 if mode["value"] == "stale" else 0)).isoformat()
        route.fulfill(json=response)

    page.route("**/api/watchlist?*", quote_route)
    page.route("**/game-watchlist.json", lambda r: r.fulfill(json=watch))
    page.route("**/player-game-projections.json", lambda r: r.fulfill(json=models))
    page.route("**/game-availability.json", lambda r: r.fulfill(json=availability))
    page.route("**/manual-order-snapshot.json", lambda r: r.fulfill(json=reference))

    def refresh():
        page.locator("#refreshOrders").click()
        page.wait_for_function("!document.querySelector('#refreshOrders').disabled", timeout=40000)

    mode["value"] = "failed"
    refresh()
    assert page.locator("#watchRows button:enabled").count() == 8
    assert page.locator("#shortlist .order-card").count() == 2
    mode["value"] = "stale"
    refresh()
    assert page.locator("#watchRows button:enabled").count() == 0
    assert page.locator("#watchRows .ticket-field").count() == 0
    assert "stale" in page.locator("#watchRows").inner_text()
    mode["value"] = "normal"
    watch["validUntil"] = models["availabilityValidUntil"] = availability["validUntil"] = "2026-10-20T01:00:00Z"
    refresh()
    assert page.locator("#watchRows button:enabled").count() == 0
    assert page.locator("#watchRows .ticket-field").count() == 0
    assert "forecast expired" in page.locator("#watchRows").inner_text()
    assert page.locator("#watchRows .row-data").first.inner_text() == "Patient limit\n—"
    assert not errors, errors
    assert not writes, writes
    browser.close()
    print(json.dumps(dict(passed=True, widths=[390, 768, 1440], liveQuotes=12, shortlist=3,
                         visibleTicketFields=True, correctDetroitSide=True, kalshiTabOpened=True,
                         budgetChanges=True, failedGameIsolated=True,
                         staleQuotesHeld=True, expiredModelsHeld=True, nonReadRequests=writes, errors=errors)))
