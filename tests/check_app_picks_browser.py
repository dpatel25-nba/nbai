"""Deterministic app switching, payout integrity, stale/held states and layout."""
import hashlib,json,os
from datetime import datetime,timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
BASE=os.environ.get('NBAI_BASE_URL','http://127.0.0.1:8770')
stamp='2026-10-20T13:00:00Z'
event={'id':'fixture','home_team':'Home','away_team':'Away','commence_time':'2026-10-20T19:00:00Z'}
players=[{'id':i,'name':f'Player {i}','team':'H' if i%2 else 'A','projectionStatus':'available','stats':{'PTS':{'mean':25,'p10':10,'p90':40}}} for i in range(1,5)]
model={'eventId':'fixture','startsAt':event['commence_time'],'homeName':'Home','awayName':'Away','players':players,'availability':{'validUntil':'2026-10-20T18:00:00Z'}}
inventory=[{'id':'fixture','sport':'basketball_nba','home':'Home','away':'Away','start':event['commence_time']}]
models={'events':[model],'schedule':[],'marketInventoryHash':hashlib.sha256(json.dumps(inventory,separators=(',',':')).encode()).hexdigest()}
quotes=[{'eventId':'fixture','provider':provider,'providerName':provider,'player':p['name'],'playerId':p['id'],'identityStatus':'matched','stat':'PTS','scope':'game','unit':'game','mainLine':True,'homeTeam':'Home','awayTeam':'Away','startsAt':event['commence_time'],'line':22.5 if provider=='prizepicks' else 27.5,'observedAt':stamp,'sourceUpdatedAt':stamp} for p in players for provider in ['prizepicks','underdog']]
market={'events':[event],'quotes':quotes,'gameQuotes':[],'generatedAt':stamp,'coverage':{'eventsChecked':1}}
with sync_playwright() as p:
 browser=p.chromium.launch(args=['--host-resolver-rules=MAP nbai.space 216.198.79.65'] if 'nbai.space' in BASE else [])
 page=browser.new_page()
 page.clock.install(time=datetime(2026,10,20,13,tzinfo=timezone.utc))
 errors=[];writes=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.on('request',lambda r:writes.append(r.url) if r.method!='GET' else None)
 page.route('**/player-consensus.json',lambda r:r.fulfill(json=market))
 page.route('**/player-game-projections.json',lambda r:r.fulfill(json=models))
 page.route('**/player-consensus-status.json',lambda r:r.fulfill(json={'success':True}))
 for width in [320,390,768,1440]:
  page.set_viewport_size({'width':width,'height':1000})
  page.goto(BASE+'/betting.html?date=2026-10-20&app=prizepicks')
  page.wait_for_selector('.app-line')
  assert page.locator('.app-line').count()==4
  assert 'More 22.5' in page.locator('.app-line').first.inner_text()
  assert page.locator('.app-combo').count()==2
  page.locator('.app-combo button').first.click()
  assert page.locator('#lineupLegs li').count()==2
  page.locator('#entryStake').fill('10');page.locator('#payoutMultiplier').fill('3')
  assert 'Total return $30.00' in page.locator('#payoutResult').inner_text()
  assert 'Net profit $20.00' in page.locator('#payoutResult').inner_text()
  assert '33.3%' in page.locator('#payoutResult').inner_text()
  page.clock.fast_forward(31000)
  assert page.locator('#payoutMultiplier').input_value()=='3', 'Unchanged refresh must preserve user scenario'
  page.locator('#entryFormat').select_option('flex')
  assert page.locator('#payoutMultiplier').input_value()==''
  page.locator('#entryStake').fill('10');page.locator('#payoutMultiplier').fill('6')
  assert 'break even' not in page.locator('#payoutResult').inner_text()
  page.locator('.app-combo button').last.click()
  assert page.locator('#lineupLegs li').count()==3
  assert page.locator('#payoutMultiplier').input_value()==''
  page.locator('#entryStake').fill('10');page.locator('#payoutMultiplier').fill('6')
  page.locator('#pickApp').select_option('underdog')
  assert page.locator('#lineupLegs li').count()==0
  assert 'Lower 27.5' in page.locator('.app-line').first.inner_text()
  assert 'More 22.5' not in page.locator('#appPicks').inner_text()
  assert page.locator('#payoutMultiplier').input_value()==''
  assert page.locator('#entryStake').is_disabled()
  assert 'Classic' in page.locator('#appMode').inner_text()
  assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  page.reload();page.wait_for_selector('.app-line')
  assert page.locator('#pickApp').input_value()=='underdog'
 # An exact quote change invalidates the lineup leg and its entered payout.
 page.locator('.app-combo button').first.click()
 page.locator('#entryStake').fill('10');page.locator('#payoutMultiplier').fill('3')
 for q in quotes:
  if q['provider']=='underdog':q['line']=28.5
 page.locator('#reloadBoard').click()
 page.wait_for_function('document.querySelector("#appLines").textContent.includes("28.5")')
 assert page.locator('#lineupLegs li').count()==0
 assert page.locator('#payoutMultiplier').input_value()==''
 for q in quotes:q['sourceUpdatedAt']='2026-10-20T00:00:00Z'
 page.locator('#reloadBoard').click()
 page.wait_for_function('document.querySelector("#appPicksStatus").textContent.includes("No current favorites verified")')
 assert 'Saved lean' in page.locator('#appLines').inner_text()
 # Detail still exposes only the selected app's picks while sportsbook research is labeled.
 page.locator('.matchup-card').click()
 assert 'Sportsbook research' in page.locator('#teamMarkets').inner_text()
 assert page.locator('#kalshiWatch').count()==0
 model['reviewRequired']='Fixture hold'
 page.locator('#reloadBoard').click()
 page.wait_for_function('document.querySelectorAll(".app-line").length===0')
 assert page.locator('.app-combo').count()==0
 assert page.locator('#entryStake').is_disabled()
 model.pop('reviewRequired')
 page.locator('#reloadBoard').click();page.wait_for_selector('.app-line')
 page.route('**/player-consensus.json',lambda r:r.fulfill(status=503,body='Unavailable'))
 page.locator('#reloadBoard').click()
 page.wait_for_function('document.querySelector("#appPicksStatus").textContent.includes("Reload failed")')
 assert not errors,errors
 assert not writes,writes
 browser.close()
print(json.dumps({'passed':True,'widths':[320,390,768,1440],'errors':errors,'non_get_requests':writes}))
