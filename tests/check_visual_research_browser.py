from pathlib import Path
import os,json,xml.etree.ElementTree as ET
from playwright.sync_api import sync_playwright
R=Path(__file__).resolve().parents[1];O=R/'data/features/website_remodel/research-gallery';O.mkdir(parents=True,exist_ok=True);BASE=os.environ.get('NBAI_BASE_URL','http://127.0.0.1:8770')
with sync_playwright() as p:
 b=p.chromium.launch(args=['--host-resolver-rules=MAP nbai.space 216.198.79.65'] if 'nbai.space' in BASE else []);page=b.new_page(accept_downloads=True);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 for width in [320,390,768,1440]:
  page.set_viewport_size({'width':width,'height':1000});page.goto(BASE+'/insights.html');page.wait_for_selector('#researchContent:not([hidden])')
  assert page.locator('#shotTeam option').count()==30
  assert page.locator('#playerChart circle[data-player]').count()==378
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.locator('#playerQuery').fill('jokic');assert page.locator('#chartPlayer option').count()==1;assert 'Jokić' in page.locator('#playerReadout').inner_text()
  page.locator('[data-chart="efficiency"]').click();assert 'True shooting' in page.locator('#playerReadout').inner_text()
  page.locator('#playerQuery').fill('no one here');assert page.locator('#chartPlayer').is_disabled();assert 'No player matches' in page.locator('#playerReadout').inner_text()
  page.locator('#playerQuery').fill('');page.locator('#impactTeam').select_option('BOS');assert page.locator('#chartPlayer option').count()>4
  page.locator('#shotTeam').select_option('BOS');assert 'BOS' in page.locator('#teamShotTitle').inner_text()
  page.locator('#shotChart circle[data-cell]').first.click(force=True);assert 'attempts' in page.locator('#shotTooltip').inner_text()
  page.locator('#shotMode').select_option('volume');assert page.locator('.court-legend').is_hidden()
  page.locator('#researchSeason').select_option('2023-24');assert page.locator('#playerChart circle[data-player]').count()==360
  assert '2023-24' in page.locator('#teamShotTitle').inner_text();assert '1,230 / 1,230' in page.locator('#researchStatus').inner_text()
  page.locator('#researchSeason').select_option('2025-26');page.locator('#impactTeam').select_option('');page.locator('[data-chart="impact"]').click();page.locator('#shotMode').select_option('relative')
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.screenshot(path=str(O/f'gallery-{width}.png'),full_page=True)
 for button,name in [('downloadPlayerChart','impact.svg'),('downloadShotChart','shots.svg')]:
  with page.expect_download() as info:page.locator('#'+button).click()
  path=O/name;info.value.save_as(path);root=ET.fromstring(path.read_text());assert root.tag.endswith('svg');assert '2025-26' in path.read_text()
 page.goto(BASE+'/index.html');page.wait_for_function('document.querySelectorAll("#homeLeaders li").length===3');assert 'WAR v4' in page.locator('#homeSource').inner_text()
 page.route('**/visual-research.json',lambda r:r.fulfill(status=503,body='unavailable'));page.goto(BASE+'/insights.html');page.wait_for_function('document.querySelector("#researchStatus").textContent.includes("unavailable")');assert page.locator('#researchContent').is_hidden()
 assert not errors,errors;b.close()
receipt={'passed':True,'base':BASE,'widths':[320,390,768,1440],'checks':['player search','impact and efficiency views','team filters','season filters','shot-cell inspection','SVG downloads','mobile overflow','homepage','fetch failure'],'errors':errors}
(O/('live-checks.json' if 'nbai.space' in BASE else 'local-checks.json')).write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
