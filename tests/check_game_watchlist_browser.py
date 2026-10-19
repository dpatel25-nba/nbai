import os,json
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];BASE=os.environ.get('NBAI_BASE_URL','http://127.0.0.1:8775')
out=ROOT/'data/features/game_stability/20261019-v1/ui';out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
 browser=p.chromium.launch(args=['--host-resolver-rules=MAP nbai.space 216.198.79.65'] if 'nbai.space' in BASE else [])
 page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 for width in [390,1440]:
  page.set_viewport_size(dict(width=width,height=1000));page.goto(BASE+'/betting.html?game=26b036ff107f3c658258eaf4a6f26228')
  page.wait_for_selector('#watchRows tr',timeout=40000);assert page.locator('#watchRows tr').count()==4
  assert '2,000 simulation scenarios' in page.locator('#watchStatus').inner_text()
  assert page.locator('#watchCombos article').count()==3
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.locator('#watchQuantity').select_option('5');page.wait_for_function("document.getElementById('watchStatus').textContent.includes('5 contracts')",timeout=40000)
  assert page.locator('#watchRows tr').count()==4
  page.locator('#kalshiWatch').screenshot(path=str(out/f'watchlist-{width}.png'))
 # A mismatched export must withhold probabilities and watch triggers.
 fixture=json.loads((ROOT/'web/game-watchlist.json').read_text());fixture['inputSignature']='mismatch'
 page.route('**/game-watchlist.json',lambda r:r.fulfill(json=fixture));page.reload();page.wait_for_selector('#watchRows tr',timeout=40000)
 assert 'Forecast expired or updating' in page.locator('#watchRows').inner_text()
 assert 'Watch price reached' not in page.locator('#watchRows').inner_text()
 assert not errors,errors;browser.close();print(json.dumps({'passed':True,'viewports':[390,1440],'quantityChange':True,'mismatchedForecastHeld':True,'consoleErrors':errors}))
