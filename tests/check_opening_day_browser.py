"""Opening-night real-data smoke check, plus an explicit stale-availability fixture."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
BASE=os.environ.get('NBAI_BASE_URL','http://127.0.0.1:8770')
DAY=os.environ.get('NBAI_GAME_DATE','2026-10-20')
OUT=ROOT/'data/features/opening_readiness_2026_10_19/browser'
OUT.mkdir(parents=True,exist_ok=True)

with sync_playwright() as p:
    browser=p.chromium.launch()
    page=browser.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    for width in [320,390,768,1440]:
        page.set_viewport_size({'width':width,'height':1000})
        page.goto(BASE+'/betting.html?date='+DAY)
        page.wait_for_selector('.matchup-card')
        assert page.locator('.matchup-card').count()==3
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        links=page.locator('.matchup-card').evaluate_all('(els)=>els.map(e=>e.href)')
        for link in links:
            page.goto(link);page.wait_for_selector('#gameDetail:not([hidden])')
            assert 'Forecast unavailable' not in page.locator('#forecastHero').inner_text()
            page.locator('.game-method summary').click()
            assert 'Reported Out:' in page.locator('#gameMethod').inner_text()
            assert 'conditional on playing' in page.locator('#gameMethod').inner_text()
            assert page.locator('#teamMarketRows tr').count()>0
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(OUT/f'game-{width}.png'),full_page=True)
    page.goto(BASE+'/betting.html?date='+DAY)
    page.wait_for_selector('.matchup-card')
    page.locator('.matchup-card').filter(has_text='Detroit Pistons').click()
    page.locator('#playerBrowser').evaluate('(e)=>e.open=true')
    page.locator('#propSearch').fill('Devin Carter')
    assert 'Reported Out' in page.locator('#propStatus').inner_text()
    assert page.locator('#propRows tr').count()==8
    assert not page.locator('#featuredProps').get_by_text('Devin Carter',exact=True).count()
    # Serve the actual projection payload with expired availability only.
    models=page.request.get(BASE+'/player-game-projections.json').json()
    for e in models['events']:e['availability']['validUntil']='2020-01-01T00:00:00Z'
    page.route('**/player-game-projections.json',lambda route:route.fulfill(json=models))
    page.reload();page.wait_for_selector('#gameDetail:not([hidden])')
    assert 'Forecast unavailable' in page.locator('#forecastHero').inner_text()
    assert 'expired' in page.locator('#forecastHero').inner_text()
    assert page.locator('#featuredProps .book-card').count()==0
    assert page.locator('#parlayCard li').count()==0
    assert not errors,errors
    browser.close()
    report=dict(passed=True,base=BASE,date=DAY,games=3,widths=[320,390,768,1440],
                out_player_excluded=True,expired_availability_withheld=True,errors=errors)
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
