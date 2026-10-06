"""Game-day table, prop discovery and same-book parlay browser checks."""
from pathlib import Path
import os,json
from playwright.sync_api import sync_playwright
R=Path(__file__).resolve().parents[1];O=R/'data/features/player_props/consensus/ui';O.mkdir(parents=True,exist_ok=True)
BASE=os.environ.get('NBAI_BASE_URL','http://127.0.0.1:8770')
NYK='/betting.html?date=2026-10-20&game=2b042ddad3d3c6c6fe5b7005dc26c775'
with sync_playwright() as p:
 browser=p.chromium.launch(args=['--host-resolver-rules=MAP nbai.space 216.198.79.65'] if 'nbai.space' in BASE else [])
 page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 for w in [320,390,768,1440]:
  page.set_viewport_size({'width':w,'height':1000});page.goto(BASE+'/betting.html?date=2026-10-20')
  page.wait_for_selector('.matchup-card');assert page.locator('.matchup-card').count()==3
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.locator('.matchup-card').filter(has_text='New York Knicks').click()
  assert page.locator('#gameDetail').is_visible();assert page.locator('#dailyBoard').is_hidden()
  assert 'Forecast unavailable' not in page.locator('#forecastHero').inner_text()
  assert page.locator('#teamMarketRows tr').count()>0
  assert page.locator('#teamMarketRows .book-card').count()==0
  page.locator('[data-market="h2h"]').click();assert '%' in page.locator('#teamMarketRows').inner_text()
  page.locator('[data-market="totals"]').click();assert page.locator('#marketSideA').inner_text()=='Over'
  assert page.locator('#featuredProps .book-card').count()==3
  assert page.locator('#parlayCard li').count()==2
  assert 'Draft only' in page.locator('#parlayCard').inner_text()
  page.locator('#featuredProps button').first.click()
  assert page.locator('#playerBrowser').evaluate('(e)=>e.open')
  assert 'Brunson' in page.locator('#selectedPlayerTitle').inner_text()
  assert page.locator('#propRows tr').count()==8
  # Search must include players without bookmaker quotes, preserving their forecasts.
  page.locator('#propSearch').fill('lebron')
  assert page.locator('#playerChoices button').count()==1
  assert 'LeBron James' in page.locator('#selectedPlayerTitle').inner_text()
  assert page.locator('#propRows tr').first.locator('td').first.inner_text().splitlines()[0]!='—'
  assert 'Line unavailable' in page.locator('#propRows').inner_text()
  page.locator('#propSearch').fill('nobody');assert page.locator('#selectedPlayerPanel').is_hidden()
  assert 'No players match' in page.locator('#playerSearchStatus').inner_text()
  page.locator('#clearPropSearch').click();assert page.locator('#playerChoices button').count()>20
  page.locator('#propSearch').fill('brunson');assert 'Brunson' in page.locator('#selectedPlayerTitle').inner_text()
  assert 'FanDuel' in page.locator('#propRows').inner_text()
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.screenshot(path=str(O/f'compact-day-detail-{w}.png'),full_page=True)
  page.reload();page.wait_for_selector('#gameDetail:not([hidden])');assert 'New York Knicks' in page.locator('#detailTitle').inner_text()
  page.locator('#backToBoard').click();assert page.locator('#dailyBoard').is_visible()
 page.goto(BASE+'/betting.html?date=2099-01-01');page.wait_for_selector('.empty-day');assert page.locator('.matchup-card').count()==0
 page.goto(BASE+'/betting.html?date=invalid');page.wait_for_selector('.matchup-card')
 page.goto(BASE+'/betting.html?date=2026-10-21&game=164cc0396837d5ff433215ffd3279283')
 page.wait_for_selector('#gameDetail:not([hidden])');assert 'Forecast unavailable' in page.locator('#forecastHero').inner_text()
 assert page.locator('#featuredProps .book-card').count()==0;assert page.locator('#parlayCard li').count()==0
 fixture=json.loads((R/'web/player-consensus.json').read_text())
 for q in fixture['quotes']+fixture['gameQuotes']:q['sourceUpdatedAt']='2020-01-01T00:00:00Z'
 # Explicit fixture: a third selectable book line agrees with the consensus lean.
 for q in fixture['quotes']:
  if q['eventId']=='2b042ddad3d3c6c6fe5b7005dc26c775' and q['stat']=='REB' and q['provider']=='fanduel':q['line']=3.5
 page.route('**/player-consensus.json',lambda r:r.fulfill(json=fixture))
 page.goto(BASE+NYK);page.wait_for_selector('#teamMarketRows tr')
 assert 'Saved / stale' in page.locator('#teamMarketRows').inner_text()
 assert 'Saved lean' in page.locator('#featuredProps').inner_text()
 assert 'Saved lines' in page.locator('#parlayCard').inner_text()
 assert page.locator('#parlayCard li').count()==3
 # Two valid priced legs yield two; one never becomes a parlay.
 fixture['quotes']=[q for q in fixture['quotes'] if q['stat']!='REB']
 page.locator('#reloadBoard').click();page.wait_for_function('document.querySelectorAll("#parlayCard li").length===2')
 fixture['quotes']=[q for q in fixture['quotes'] if q['stat']=='PTS']
 page.locator('#reloadBoard').click();page.wait_for_function('document.querySelectorAll("#parlayCard li").length===0')
 assert 'No supported parlay' in page.locator('#parlayCard').inner_text()
 page.route('**/player-consensus.json',lambda r:r.fulfill(status=503,body='Unavailable'))
 page.locator('#reloadBoard').click();page.wait_for_function('document.querySelector("#boardStatus").textContent.includes("Reload failed")')
 assert page.locator('#featuredProps .book-card').count()==1
 assert not errors,errors
 browser.close()
print(json.dumps({'passed':True,'widths':[320,390,768,1440],'errors':errors,'checks':['compact tables','unquoted player search','three/two/insufficient parlay legs','stale labels','held forecasts','reload failure']}))
