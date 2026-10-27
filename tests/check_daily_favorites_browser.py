"""Verify the current ranked card has exactly three visible favorites per game."""
import json, os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get('NBAI_BASE_URL', 'http://127.0.0.1:8785')
research = json.loads((ROOT/'web/research-picks.json').read_text())
date = research['automation']['dateEt']
# Eastern date matters for games after midnight UTC.
from datetime import datetime
from zoneinfo import ZoneInfo
games = [g for g in research['games'] if g.get('selectionMode') == 'ranked_favorites'
         and datetime.fromisoformat(g['officialStartsAt']).astimezone(ZoneInfo('America/New_York')).date().isoformat() == date]
assert games and all(len(g['picks']) == 3 for g in games)
OUT = ROOT/'data/features/daily_research_publication'/date/'browser'
OUT.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    options = {'executable_path': os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE']} if os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') else {}
    browser = p.chromium.launch(**options)
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    if not os.environ.get('NBAI_LIVE'):
        quotes = json.loads((ROOT/'web/kalshi-board-quotes.json').read_text())
        page.route('**/api/kalshi-board', lambda r: r.fulfill(json=quotes))
    for width in [390, 1440]:
        page.set_viewport_size({'width': width, 'height': 1000})
        for home in ['index.html', 'betting.html']:
            page.goto(BASE+'/'+home+'?date='+date)
            page.wait_for_selector('#gameCards .favorite-card')
            assert page.locator('#gameCards .matchup-picks').count() == len(games)
            assert page.locator('#gameCards .favorite-card').count() == 3*len(games)
            assert not page.locator('#dailyPicks').is_visible()
            for game in games:
                section = page.locator('#gameCards [data-game-id="'+game['gameId']+'"]')
                assert section.locator('.favorite-card').count() == 3
                assert set(section.locator('.favorite-card h3').all_text_contents()) == {x['label'] for x in game['picks']}
            assert page.locator('.pick-state:visible, .price-limit:visible').count() == 0
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(OUT/f'home-{width}.png'), full_page=True)
        for game in games:
            page.goto(BASE+'/matchup.html?game='+game['gameId']+'&date='+date)
            page.wait_for_selector('#gameFavorites .favorite-card')
            assert page.locator('#gameFavorites .favorite-card').count() == 3
            assert set(page.locator('#gameFavorites .favorite-card h3').all_text_contents()) == {x['label'] for x in game['picks']}
            assert page.locator('.pick-state:visible, .price-limit:visible, .empty-picks:visible').count() == 0
            assert not page.locator('#winnerCards').is_visible()
            assert not page.locator('#comboCard').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('#gameFavorites .pick-evidence summary').first.click()
        assert page.locator('#gameFavorites .pick-evidence').first.get_attribute('open') is not None
        page.screenshot(path=str(OUT/f'matchup-{width}.png'), full_page=True)
    assert not errors, errors
    browser.close()
print(json.dumps({'passed': True, 'date': date, 'games': len(games), 'picks': len(games)*3, 'widths': [390, 1440], 'base': BASE}))
