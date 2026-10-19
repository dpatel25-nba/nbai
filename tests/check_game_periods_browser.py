"""Exercise every market/period using actual saved quotes; no betting actions."""
import json,os
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
BASE=os.environ.get('NBAI_BASE_URL','http://127.0.0.1:8770')
market=json.loads((ROOT/'web/player-consensus.json').read_text())
out=ROOT/'data/features/forecast_audit/20261019-calibration/ui';out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
 browser=p.chromium.launch(args=['--host-resolver-rules=MAP nbai.space 216.198.79.65'] if 'nbai.space' in BASE else []);page=browser.new_page();errors=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 for width in (390,1440):
  page.set_viewport_size({'width':width,'height':1000})
  for event in market['events'][:3]:
   page.goto(BASE+'/betting.html?game='+event['id'])
   page.wait_for_selector('#teamMarketRows .line-value')
   assert 'Updated simulator' in page.locator('#modelStatus').inner_text()
   assert '512 warm-up games across 4 passes' in page.locator('#calibrationMethod').text_content()
   assert 'Forecast unavailable' not in page.locator('#forecastHero').inner_text()
   for period in ('game','h1','h2'):
    page.locator('[data-period="'+period+'"]').click()
    for kind in ('spreads','totals','h2h'):
     page.locator('[data-market="'+kind+'"]').click()
     expected=[q for q in market['gameQuotes'] if q['eventId']==event['id'] and q.get('period','game')==period and q['market']==kind]
     assert page.locator('#teamMarketRows tr').count()==max(1,len(expected))
     if kind=='totals':assert page.locator('#marketSideA').inner_text()=='Over'
     if period!='game':
      assert 'Not modeled yet' in page.locator('#gameMarketForecast').inner_text()
      if expected:assert page.locator('#teamMarketRows .model-difference').all_inner_texts()==['No comparison\nPeriod forecast not available']*len(expected)
     if expected:
      q=expected[0];assert q['providerName'] in page.locator('#teamMarketRows tr').first.inner_text()
      assert page.locator('#teamMarketRows .line-value').count()==2*len(expected)
     assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    if event==market['events'][0]:
     page.locator('[data-market="totals"]').click()
     page.locator('#teamMarkets').screenshot(path=str(out/f'{period}-{width}.png'))
   page.locator('[data-period="game"]').click();page.locator('[data-market="spreads"]').click()
   assert 'Not modeled yet' not in page.locator('#gameMarketForecast').inner_text()
 # Empty data must remain explicit rather than inventing a half line.
 empty={**market,'gameQuotes':[]}
 page.route('**/player-consensus.json',lambda route:route.fulfill(json=empty))
 page.reload();page.wait_for_selector('#teamMarketRows tr')
 page.locator('[data-period="h2"]').click()
 assert 'No second-half spread lines returned' in page.locator('#teamMarketRows').inner_text()
 assert not errors,errors
 browser.close()
 print(json.dumps({'passed':True,'viewports':[390,1440],'games':3,'periods':3,'markets':3,'consoleErrors':errors}))
