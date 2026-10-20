"""Smoke-test the reviewed-picks publication and price/availability presentation."""
import copy,json,os
from pathlib import Path
from datetime import datetime,timezone
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
BASE=os.environ.get('NBAI_BASE_URL','http://127.0.0.1:8776')
OUT=ROOT/'data/features/fundamental_prop_research/20261020-site/browser';OUT.mkdir(parents=True,exist_ok=True)
model=json.loads((ROOT/'web/kalshi-board-model.json').read_text())
research=json.loads((ROOT/'web/research-picks.json').read_text())
quotes=json.loads((ROOT/'web/kalshi-board-quotes.json').read_text())
NOW='2026-10-20T18:55:00Z'
for q in quotes['markets']:q['observedAt']=NOW
quotes['generatedAt']=NOW
state={'model':model,'research':research,'quotes':quotes,'quote_fail':False,'research_fail':False}
with sync_playwright() as p:
 browser=p.chromium.launch(**({"executable_path":os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]} if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") else {}));page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.clock.install(time=datetime(2026,10,20,18,55,tzinfo=timezone.utc));page.clock.pause_at(datetime(2026,10,20,18,55,tzinfo=timezone.utc))
 def serve(route,key,fail=None):
  if fail and state[fail]:route.fulfill(status=503,body='Unavailable')
  else:route.fulfill(json=state[key])
 page.route('**/kalshi-board-model.json',lambda r:serve(r,'model'))
 page.route('**/research-picks.json',lambda r:serve(r,'research','research_fail'))
 page.route('**/api/kalshi-board',lambda r:serve(r,'quotes','quote_fail'))
 page.route('**/kalshi-board-quotes.json',lambda r:serve(r,'quotes'))
 for width in [320,390,768,1440]:
  page.set_viewport_size({'width':width,'height':1000});page.goto(BASE+'/index.html?date=2026-10-20')
  page.wait_for_selector('#dailyFavorites .favorite-card');assert page.locator('#dailyFavorites .favorite-card').count()==3
  assert page.locator('#dailyCombo li').count()==3
  assert page.locator('#gameTabs a').count()==3
  assert page.locator('.site-nav a[aria-current="page"]').inner_text()=='Home'
  text=page.locator('main').inner_text()
  for hidden in ['Model chance','Model margin','Joint model chance','Projected total','Home win share']:assert hidden not in text,hidden
  assert 'Combo price & payout unverified' in text
  assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  page.screenshot(path=str(OUT/f'home-{width}.png'),full_page=True)
  for i in range(3):
   page.locator('#gameTabs a').nth(i).click();page.wait_for_selector('#gameFavorites .favorite-card')
   assert page.locator('#gameFavorites .favorite-card').count()==3
   assert page.locator('#comboCard li').count()==3
   assert page.locator('#winnerCards .winner-card').count()==2
   assert page.locator('#gameTabs a[aria-current="page"]').count()==1
   assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  page.screenshot(path=str(OUT/f'matchup-{width}.png'),full_page=True)
 page.goto(BASE+'/matchup.html?game=0022600001&date=2026-10-20');page.wait_for_selector('#gameFavorites .favorite-card')
 page.locator('.pick-evidence summary').first.click();page.clock.fast_forward(10000)
 assert page.locator('.pick-evidence').first.get_attribute('open') is not None
 page.locator('#playerSearch').fill('White');assert page.locator('.player-lines').count()==1
 assert 'Not reviewed' in page.locator('#allPlayerLines').inner_text()
 assert 'Model chance' not in page.locator('#allPlayerLines').inner_text()
 # Failure retains timestamps; freshness expires even with an evidence panel open.
 state['quote_fail']=True;page.clock.fast_forward(80000)
 assert page.locator('#gameFavorites .ready').count()==0
 assert 'saved' in page.locator('#gameFavorites').inner_text()
 # At tip, archived picks remain explicitly closed, never current recommendations.
 page.clock.fast_forward(5*60*1000)
 assert page.locator('#gameFavorites .closed').count()==3
 assert 'SAVED PREGAME SCRIPT' in page.locator('#comboCard').inner_text()
 # No research means no model-only fallback. Invalidate the retained cache by navigation.
 state['research_fail']=True;page.goto(BASE+'/betting.html?date=2026-10-20')
 page.wait_for_function("document.querySelector('#dailyFavorites').textContent.includes('Research is pending')")
 assert page.locator('#dailyFavorites .favorite-card').count()==0
 assert not errors,errors
 browser.close()
print(json.dumps({'passed':True,'widths':[320,390,768,1440],'games':3,'errors':errors}))
