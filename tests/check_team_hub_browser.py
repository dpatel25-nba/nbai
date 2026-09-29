"""Team hub integration: weekly league progression, perspectives and responsive layout."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = os.environ.get('NBAI_BASE_URL', 'http://127.0.0.1:8767').rstrip('/')
OUT = Path('/tmp/nbai-team-hub')
OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width':1440,'height':1000}, accept_downloads=True)
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(BASE+'/season.html')
    page.wait_for_selector('#weekDays .calendar-day')
    page.locator('#myTeam').select_option('OKC')
    assert page.locator('#teamName').inner_text() == 'Oklahoma City Thunder'
    assert page.locator('#rosterTeam').input_value() == 'OKC'
    assert page.locator('#gamesTeam').input_value() == 'OKC'
    assert page.locator('#statsTeam').input_value() == 'OKC'
    assert page.locator('#weekDays .calendar-day').count() == 7
    page.reload()
    assert page.locator('#myTeam').input_value() == 'OKC'
    page.screenshot(path=str(OUT/'opening-desktop.png'), full_page=True)
    weeks = page.evaluate('NBAI_CALENDAR.weeks(NBAI_SEASON_DATA.schedule)')
    boundaries = []
    # Progress the actual UI week by week through every team and all 1,230 games.
    for i, week in enumerate(weeks):
        assert page.locator('#runWeek').is_enabled(), i
        page.locator('#runWeek').click()
        page.wait_for_function("document.getElementById('runStatus').textContent !== 'Simulating'", timeout=120000)
        assert not page.locator('#message').is_visible()
        with page.expect_download() as event:
            page.locator('#download').click()
        path = OUT/'weekly.json'
        event.value.save_as(str(path))
        exported = json.loads(path.read_text())
        games = exported['scenario']['games']
        assert all(g['date'] <= week['end'] for g in games), (i, 'advanced past Sunday')
        expected = page.evaluate("end => NBAI_SEASON.makeSchedule(NBAI_SEASON_DATA,true).filter(g=>g.date<=end).length",week['end'])
        assert len(games) == expected, (i,len(games),expected)
        assert exported['baseline']['completed'] == len(games)
        boundaries.append(len(games))
        if i == 0:
            assert page.locator('#runStatus').inner_text() == 'Paused'
            assert page.locator('#weekDays button[data-game]').count() > 0
            page.locator('#weekDays button[data-game]').first.click()
            assert page.locator('#boxDialog').is_visible()
            page.locator('#closeBox').click()
            progress = page.locator('#progressText').inner_text()
            page.locator('#myTeam').select_option('NYK')
            assert page.locator('#progressText').inner_text() == progress
            assert 'New York' in page.locator('#teamName').inner_text()
            page.locator('#nextWeek').click()
            assert page.locator('#weekDays button[data-game]').count() == 0
            page.locator('#prevWeek').click()
            assert page.locator('#progressText').inner_text() == progress
    assert len(games) == 1230
    assert page.locator('#runWeek').is_disabled()
    assert page.locator('#nextWeek').is_disabled()
    standings = {t['team']: t for t in exported['scenario']['standings']}
    for team, row in standings.items():
        page.locator('#myTeam').select_option(team)
        assert page.locator('#teamRecord').inner_text() == f"{row['w']}–{row['l']}"
        assert row['w'] + row['l'] == 82
        assert page.locator('#gamesMetric').inner_text() == '82 / 82'
        assert page.locator('#recentForm .form-chip').count() == 5
        assert page.locator('#nextOpponent').inner_text() == 'Regular season complete'
    page.locator('#myTeam').select_option('NYK')
    page.locator('#tab-players').click()
    assert page.locator('#statsTeam').input_value() == 'NYK'
    assert page.locator('#playersBody tr').count() == sum(p['team']=='NYK' for p in exported['scenario']['players'])
    page.locator('#tab-standings').click()
    assert page.locator('.my-team-row').count() == 1
    page.screenshot(path=str(OUT/'completed-desktop.png'),full_page=True)
    for width in [320,390,768,1440]:
        page.set_viewport_size({'width':width,'height':900})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        assert page.evaluate("[...document.querySelectorAll('.calendar-day, .metrics strong')].every(e=>e.scrollWidth<=e.clientWidth)"), width
        page.screenshot(path=str(OUT/f'completed-{width}.png'),full_page=True)
    # Editing the selected roster is a preseason scenario and restarts both the calendar and stats.
    page.locator('#playerSearch').fill('Joki')
    page.locator('#addPlayer').click()
    assert page.locator('#teamRecord').inner_text() == '0–0'
    assert page.locator('#weekTitle').inner_text().startswith('Week 1 /')
    assert '1 moved' in page.locator('#editCount').inner_text()
    assert page.locator('#myTeam').input_value() == 'NYK'
    page.locator('#runWeek').click()
    page.wait_for_function("document.getElementById('runStatus').textContent === 'Paused'",timeout=120000)
    with page.expect_download() as event:
        page.locator('#download').click()
    event.value.save_as(str(OUT/'edited-week.json'))
    edited = json.loads((OUT/'edited-week.json').read_text())
    assert edited['baseline']['membership'] != edited['scenario']['membership']
    assert edited['baseline']['completed'] == edited['scenario']['completed'] == boundaries[0]
    assert not errors, errors
    print(json.dumps({'passed':True,'weekly_boundaries':boundaries,'teams_verified':len(standings),'games':len(games),'browser_errors':errors,'screenshots':str(OUT)}))
    browser.close()
