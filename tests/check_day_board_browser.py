"""Deterministic checks for the Kalshi daily shortlist and dedicated matchup page."""
import copy
import json
import os
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

BASE = os.environ.get("NBAI_BASE_URL", "http://127.0.0.1:8776")
NOW = "2026-10-20T14:00:00Z"
N = 200
players = [
    dict(id=i, name=f"Player {i}", team="BOS" if i == 1 else "DET",
         projectionStatus="available", injuryStatus="Not listed",
         values={stat: [30 if j < 150 else 10 for j in range(N)]
                 if stat == "PTS" else [1] * N
                 for stat in ["PTS", "REB", "AST", "STL", "BLK", "FG3M", "FTM"]})
    for i in [1, 2, 3]
]
game = dict(gameId="g", eventId="legacy", home="DET", away="BOS",
            homeName="Detroit Pistons", awayName="Boston Celtics",
            startsAt="2026-10-20T19:00:00Z", reviewRequired=None, players=players,
            scores=[[110, 100] if i < 150 else [90, 100] for i in range(N)],
            gameProjection=dict(homeScore=dict(mean=105), awayScore=dict(mean=100),
                                homeMargin=dict(mean=5), homeWinShare=.75,
                                total=dict(mean=205, p10=190, p90=210)))
model = dict(schemaVersion=1, generatedAt=NOW, runs=N,
             validUntil="2026-10-20T18:00:00Z", games=[game], schedule=[])
markets = [
    dict(ticker=f"p{i}", kind="prop", stat="PTS", player=f"Player {i}",
         threshold=24.5, identity=dict(date="2026-10-20", teams=["BOS", "DET"]),
         title=f"Player {i}: 25+ points", yesAsk=.3, noAsk=.75,
         yesSize=5, noSize=10, quoteValid=True, observedAt=NOW,
         url="https://kalshi.com/markets/example")
    for i in [1, 2, 3, 4]  # Player 4 deliberately has no model.
]
markets.append(dict(ticker="winner", kind="winner",
                    identity=dict(date="2026-10-20", teams=["BOS", "DET"], yesTeam="DET"),
                    title="Detroit wins", yesAsk=.4, noAsk=.65, yesSize=10, noSize=10,
                    quoteValid=True, observedAt=NOW, url="https://kalshi.com/markets/example"))
quotes = dict(schemaVersion=1, generatedAt=NOW, markets=markets, scope="Test inventory",
              coverage=[dict(series="KXNBAPTS", complete=True, accepted=4, rawCount=4)])
state = dict(model=model, quotes=quotes, model_fail=False, quote_fail=False)

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--host-resolver-rules=MAP nbai.space 216.198.79.65"]
                                if "nbai.space" in BASE else [])
    page = browser.new_page()
    page.clock.install(time=datetime(2026, 10, 20, 14, tzinfo=timezone.utc))
    page.clock.pause_at(datetime(2026, 10, 20, 14, tzinfo=timezone.utc))
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def fulfill(route, key, fail_key=None):
        if fail_key and state[fail_key]:
            route.fulfill(status=503, body="Unavailable")
        else:
            route.fulfill(json=state[key])

    page.route("**/kalshi-board-model.json", lambda r: fulfill(r, "model", "model_fail"))
    page.route("**/api/kalshi-board", lambda r: fulfill(r, "quotes", "quote_fail"))
    page.route("**/kalshi-board-quotes.json", lambda r: fulfill(r, "quotes"))
    for width in [320, 390, 768, 1440]:
        page.set_viewport_size(dict(width=width, height=1000))
        page.goto(BASE + "/betting.html?date=2026-10-20")
        page.wait_for_selector("#dailyFavorites .favorite-card")
        assert page.locator("#dailyFavorites .favorite-card").count() == 3
        assert len(set(page.locator("#dailyFavorites h3").all_text_contents())) == 3
        assert "30¢" in page.locator("#dailyFavorites").inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator(".matchup-card").click()
        page.wait_for_selector("#gameFavorites .favorite-card")
        assert "/matchup.html?" in page.url
        assert page.locator("#gameFavorites .favorite-card").count() == 3
        assert page.locator("#winnerCards .winner-card").count() == 2
        assert 'NO on “Detroit wins”' in page.locator("#winnerCards").inner_text()
        assert page.locator("#comboCard li").count() == 3
        assert "75.0%" in page.locator("#comboCard").inner_text()  # Joint, not 0.75^3
        assert "Not available" in page.locator("#comboCard").inner_text()
        page.locator("#playerSearch").fill("Player 4")
        assert page.locator(".player-lines").count() == 1
        assert "Player identity not matched" in page.locator("#allPlayerLines").inner_text()
        page.locator("#statFilter").select_option("AST")
        assert "No lines match" in page.locator("#allPlayerLines").inner_text()
        page.locator("#statFilter").select_option("PTS")
        assert page.locator(".player-lines").count() == 1
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#backToBoard").click()
        page.wait_for_selector(".matchup-card")

    # Failed refresh retains dated observations, then clears favorites after expiry.
    state["quote_fail"] = True
    page.locator("#refreshBoard").click()
    page.wait_for_function("document.querySelector('#quoteStatus').textContent.includes('Refresh failed')")
    assert page.locator("#dailyFavorites .favorite-card").count() == 3
    page.clock.fast_forward(80000)
    page.wait_for_function("document.querySelectorAll('#dailyFavorites .favorite-card').length === 0")
    assert "saved" in page.locator("#gameCards").inner_text()
    state["quote_fail"] = False
    state["quotes"] = copy.deepcopy(quotes)
    state["quotes"]["generatedAt"] = "2026-10-20T14:01:20Z"
    for q in state["quotes"]["markets"]:
        q["observedAt"] = state["quotes"]["generatedAt"]

    # Fresh prices do not rescue an expired forecast.
    state["model"] = copy.deepcopy(model)
    state["model"]["validUntil"] = NOW
    page.goto(BASE + "/matchup.html?game=g")
    page.wait_for_selector("#gameContent")
    assert "Forecast on hold" in page.locator("#forecastHero").inner_text()
    assert page.locator("#gameFavorites .favorite-card").count() == 0
    assert "40¢" in page.locator("#winnerCards").inner_text()
    assert "No supported combo" in page.locator("#comboCard").inner_text()
    state["model"] = model
    page.goto(BASE + "/matchup.html?game=unknown")
    page.wait_for_selector("#missingGame")
    assert page.locator("#gameContent").is_hidden()
    page.goto(BASE + "/betting.html?date=2099-01-01")
    page.wait_for_selector(".empty-day")
    assert page.locator(".matchup-card").count() == 0
    page.goto(BASE + "/betting.html?game=legacy&date=2026-10-20")
    page.wait_for_selector("#gameContent")
    assert "/matchup.html?" in page.url

    # Initial service failures are explicit, rather than leaving a loading skeleton.
    state["model_fail"] = True
    page.goto(BASE + "/betting.html")
    page.wait_for_function("document.querySelector('#modelStatus').textContent.includes('Forecast unavailable')")
    assert "temporarily unavailable" in page.locator("#gameCards").inner_text()
    assert "Checking" not in page.locator("#dailyFavorites").inner_text()
    state["model_fail"] = False
    state["quotes"]["markets"] = []
    page.goto(BASE + "/matchup.html?game=g")
    page.wait_for_selector("#gameContent")
    assert "No recognized player markets" in page.locator("#allPlayerLines").inner_text()
    assert page.locator("#gameFavorites .favorite-card").count() == 0
    assert not errors, errors
    browser.close()
print(json.dumps(dict(passed=True, widths=[320, 390, 768, 1440],
                      checks=["favorites", "dedicated pages", "exact sides", "joint combo",
                              "unmatched players", "search", "stale quotes", "expired forecast",
                              "service failures", "legacy links"], errors=errors)))
