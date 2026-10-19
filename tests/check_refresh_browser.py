"""Exercise partial refreshes, mixed deployments and recovery in the real UI."""
import copy
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get('NBAI_BASE_URL', 'http://127.0.0.1:8770')
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/features/opening_readiness_2026_10_19/refresh-browser.json'

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    market = page.request.get(BASE + '/player-consensus.json').json()
    models = page.request.get(BASE + '/player-game-projections.json').json()
    status = page.request.get(BASE + '/player-consensus-status.json').json()
    day = market['events'][0]['commence_time'][:10]
    page.goto(BASE + '/betting.html?date=' + day)
    page.wait_for_selector('.matchup-card')
    page.locator('.matchup-card').first.click()
    original_hero = page.locator('#forecastHero').inner_text()
    original_status = page.locator('#boardStatus').inner_text()

    def reload_board():
        page.locator('#reloadBoard').click()
        page.wait_for_function('!document.querySelector("#reloadBoard").disabled')

    def failed_retaining_original():
        reload_board()
        assert 'Reload failed' in page.locator('#boardStatus').inner_text()
        assert original_status in page.locator('#boardStatus').inner_text()
        assert page.locator('#forecastHero').inner_text() == original_hero

    # A successful model download must not replace half of the last good pair.
    changed = copy.deepcopy(models)
    for event in changed['events']:
        event['reviewRequired'] = 'Injected model from another refresh'
    page.route('**/player-game-projections.json', lambda r: r.fulfill(json=changed))
    page.route('**/player-consensus.json', lambda r: r.fulfill(status=503, body='Unavailable'))
    failed_retaining_original()
    page.unroute_all()

    # A successful quote download must not replace the other half either.
    changed_market = copy.deepcopy(market)
    changed_market['generatedAt'] = '2020-01-01T00:00:00Z'
    page.route('**/player-consensus.json', lambda r: r.fulfill(json=changed_market))
    page.route('**/player-game-projections.json', lambda r: r.fulfill(status=503, body='Unavailable'))
    failed_retaining_original()
    page.unroute_all()

    # Both downloads succeed, but belong to different game inventories.
    changed_market['events'][0]['commence_time'] = '2099-01-01T00:00:00Z'
    page.route('**/player-consensus.json', lambda r: r.fulfill(json=changed_market))
    failed_retaining_original()
    page.unroute_all()

    # Missing collection status also retains the complete saved pair.
    page.route('**/player-consensus-status.json', lambda r: r.fulfill(status=503, body='Unavailable'))
    failed_retaining_original()
    page.unroute_all()

    status['success'] = False
    page.route('**/player-consensus-status.json', lambda r: r.fulfill(json=status))
    reload_board()
    assert 'Latest collection failed' in page.locator('#boardStatus').inner_text()
    assert 'Reload failed' not in page.locator('#boardStatus').inner_text()
    assert page.locator('#forecastHero').inner_text() == original_hero
    page.unroute_all()
    reload_board()
    assert page.locator('#boardStatus').inner_text() == original_status

    # Without a saved pair, a failed initial load must not display partial data.
    page.route('**/player-game-projections.json', lambda r: r.fulfill(status=503, body='Unavailable'))
    page.reload()
    page.wait_for_function('document.querySelector("#boardStatus").textContent.includes("Market data unavailable")')
    assert page.locator('.matchup-card').count() == 0
    assert page.locator('#gameDetail').is_hidden()
    page.unroute_all()
    reload_board()
    assert page.locator('#forecastHero').inner_text() == original_hero

    # The independent simulator keeps its historical inputs visibly dated.
    for width in [320, 390, 768, 1440]:
        page.set_viewport_size({'width': width, 'height': 1000})
        page.goto(BASE + '/game.html')
        page.wait_for_function('document.querySelector("#simSnapshot").textContent.includes("Saved roster scenario")')
        assert '2026-09-16' in page.locator('#simSnapshot').inner_text()
        assert 'do not track current roster moves' in page.locator('#simSnapshot').inner_text()
        assert 'no reported injuries' not in page.locator('#simulator').inner_text().lower()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#simRun1').click()
    page.wait_for_function('!document.querySelector("#simRun1").disabled', timeout=60000)
    assert 'Simulate another game' in page.locator('#simRun1').inner_text()
    assert not errors, errors
    browser.close()
    report = dict(passed=True, base=BASE, partial_refresh_retained=True,
                  mismatched_inventory_rejected=True, upstream_failure_visible=True,
                  initial_failure_and_recovery=True, simulator_snapshot_dated=True,
                  simulator_game_completed=True, widths=[320, 390, 768, 1440], errors=errors)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
