"""Optional Playwright end-to-end check against the local static server."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get('NBAI_BASE_URL', 'http://127.0.0.1:8765').rstrip('/')

OUT = Path('/tmp/nbai-season-browser')
OUT.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width':1440,'height':1050},accept_downloads=True)
    errors = []
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(BASE_URL + '/season.html')
    page.wait_for_selector('#rosterList .roster-row')
    assert page.locator('#standingsBody tr').count() == 15
    assert page.locator('#progressText').inner_text() == '0 of 1,230 games'
    page.screenshot(path=str(OUT/'opening.png'),full_page=True)
    page.locator('#runDay').click()
    page.wait_for_function("document.getElementById('runStatus').textContent === 'Paused'")
    assert page.locator('#progressText').inner_text() == '3 of 1,230 games'
    page.locator('#tab-games').click()
    page.locator('.game-row:not([disabled])').first.click()
    assert page.locator('#boxDialog').is_visible()
    assert page.locator('.box-total').count() == 2
    page.locator('#closeBox').click()
    page.locator('#tab-players').click()
    page.locator('#statsTeam').select_option('')
    assert page.locator('#playersBody tr').count() == 586
    page.locator('#statsMode').select_option('total')
    page.locator('#statsSearch').fill('Jok')
    assert page.locator('#playersBody tr').count() >= 1
    page.locator('#statsSearch').fill('')
    page.locator('#tab-standings').click()
    page.locator('#runSeason').click()
    page.locator('#pause').click()
    page.wait_for_function("document.getElementById('runStatus').textContent === 'Paused'")
    page.locator('#rosterTeam').select_option('WAS')
    page.locator('#playerSearch').fill('Joki')
    if page.locator('#playerChoice option').count() == 0:
        page.locator('#playerSearch').fill('Jokić')
    page.locator('#addPlayer').click()
    assert page.locator('#progressText').inner_text() == '0 of 1,230 games'
    assert '1 moved' in page.locator('#editCount').inner_text()
    page.reload()
    page.wait_for_selector('#rosterList .roster-row')
    assert '1 moved' in page.locator('#editCount').inner_text()
    page.locator('#rosterTeam').select_option('WAS')
    assert 'Nikola' in page.locator('#rosterList').inner_text()
    page.locator('#runSeason').click()
    page.wait_for_function("document.getElementById('runStatus').textContent === 'Complete'",timeout=300000)
    assert page.locator('#progressText').inner_text() == '1,230 of 1,230 games'
    assert not page.locator('#message').is_visible(),page.locator('#message').inner_text()
    page.locator('[data-conference=""]').click()
    assert page.locator('#standingsBody tr').count() == 30
    with page.expect_download() as event:
        page.locator('#download').click()
    event.value.save_as(str(OUT/'season.json'))
    exported = json.loads((OUT/'season.json').read_text())
    scenario, baseline = exported['scenario'],exported['baseline']
    assert scenario['completed'] == baseline['completed'] == 1230
    assert all(t['w']+t['l'] == 82 for t in scenario['standings'])
    assert scenario['seed'] == baseline['seed']
    assert sum(t['w'] for t in scenario['standings']) == 1230
    page.screenshot(path=str(OUT/'completed.png'),full_page=True)
    page.locator('#tab-games').click()
    page.locator('#gamesTeam').select_option('WAS')
    page.locator('#gamesMonth').select_option('2026-12')
    assert 'PROVISIONAL' in page.locator('#gameList').inner_text()
    page.locator('.game-row:not([disabled])').first.click()
    assert 'Nikola' in page.locator('#boxContent').inner_text()
    page.locator('#closeBox').click()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'page overflow on mobile'
    page.screenshot(path=str(OUT/'mobile.png'),full_page=True)
    page.locator('.season-options summary').click()
    page.locator('#completeSchedule').uncheck()
    assert page.locator('#progressText').inner_text() == '0 of 1,200 games'
    page.evaluate("const saved=JSON.parse(localStorage.getItem('nbai-season-scenario-v1')); saved.hash='outdated-roster'; localStorage.setItem('nbai-season-scenario-v1',JSON.stringify(saved));")
    page.reload()
    assert page.locator('#editCount').inner_text() == 'Original', 'stale saved roster must not replace newer input rosters'
    page.goto(BASE_URL + '/index.html')
    assert page.locator('a[href="season.html"]').count() == 1
    page.locator('#simRun1').click()
    page.wait_for_selector('#simBoxPanel',state='visible',timeout=30000)
    assert not errors,errors
    print(json.dumps({'passed':True,'browser_errors':errors,'games_per_run':1230,
        'scenario_and_baseline_complete':True,'screenshots':str(OUT)}))
    browser.close()
