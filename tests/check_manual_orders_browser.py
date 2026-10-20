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
    context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
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
    page.locator("#shortlist button").first.click()
    copied = page.evaluate("navigator.clipboard.readText()")
    assert "BUY NO" in copied and "BOSPPRITCHARD11-15" in copied and "not an order" in copied
    before = page.locator("#shortlist .size-grid").first.inner_text()
    page.locator("#orderBudget").fill("20")
    assert page.locator("#shortlist .size-grid").first.inner_text() != before
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
    assert "stale" in page.locator("#watchRows").inner_text()
    mode["value"] = "normal"
    watch["validUntil"] = models["availabilityValidUntil"] = availability["validUntil"] = "2026-10-20T01:00:00Z"
    refresh()
    assert page.locator("#watchRows button:enabled").count() == 0
    assert "forecast expired" in page.locator("#watchRows").inner_text()
    assert page.locator("#watchRows .row-data").first.inner_text() == "Patient limit\n—"
    assert not errors, errors
    assert not writes, writes
    browser.close()
    print(json.dumps(dict(passed=True, widths=[390, 768, 1440], liveQuotes=12, shortlist=3,
                         exactNoSideCopied=True, budgetChanges=True, failedGameIsolated=True,
                         staleQuotesHeld=True, expiredModelsHeld=True, nonReadRequests=writes, errors=errors)))
