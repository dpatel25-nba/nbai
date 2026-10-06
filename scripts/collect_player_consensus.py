"""Bounded, read-only NBA player-line snapshots for the Research page.

Uses existing Odds API credits; no purchases, orders or background polling.
Run with --collect; without that flag no network requests are made.
"""
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode
import argparse, hashlib, json, math, os
import requests
ROOT=Path(__file__).resolve().parents[1]
BASE='https://api.the-odds-api.com/v4'
MARKETS={'player_points':'PTS','player_assists':'AST','player_rebounds':'REB','player_steals':'STL','player_blocks':'BLK'}
BOOKS={'fanduel':'FanDuel','prizepicks':'PrizePicks','draftkings':'DraftKings','betmgm':'BetMGM','fanatics':'Fanatics','betrivers':'BetRivers','williamhill_us':'Caesars','underdog':'Underdog','espnbet':'theScore Bet','hardrockbet':'Hard Rock Bet'}
SPORTS={'basketball_nba':'Regular season','basketball_nba_preseason':'Preseason'}
DFS={'prizepicks','underdog'}
def stamp():return datetime.now(timezone.utc).isoformat()
def normalize(event,receipt):
    if event.get('sport_key') not in SPORTS:return []
    output=[]
    for book in event.get('bookmakers',[]):
        if book.get('key') not in BOOKS:continue
        for market in book.get('markets',[]):
            if market.get('key') not in MARKETS:continue
            grouped={}
            for outcome in market.get('outcomes',[]):
                name,line,side=outcome.get('description'),outcome.get('point'),outcome.get('name')
                if not name or type(line) not in (int,float) or not math.isfinite(line) or line<0 or side not in ('Over','Under'):continue
                grouped.setdefault(name,[]).append(outcome)
            for name,values in grouped.items():
                # Multiple thresholds, missing sides, or duplicates are not a main-line consensus.
                if len(values)!=2 or {v['name'] for v in values}!={'Over','Under'} or len({v['point'] for v in values})!=1:continue
                updated=market.get('last_update')
                try:
                    if not updated or datetime.fromisoformat(updated.replace('Z','+00:00')).tzinfo is None:continue
                except ValueError:continue
                odds={v['name'].lower():v.get('price') for v in values} if book['key'] not in DFS else None
                if odds and any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)<100 for v in odds.values()):odds=None
                output.append(dict(eventId=event['id'],homeTeam=event['home_team'],awayTeam=event['away_team'],startsAt=event['commence_time'],
                    scope='game',unit='game',sportKey=event['sport_key'],competition=SPORTS[event['sport_key']],player=name,stat=MARKETS[market['key']],provider=book['key'],providerName=BOOKS[book['key']],
                    line=values[0]['point'],americanOdds=odds,sourceUpdatedAt=updated,observedAt=receipt['receivedAt'],
                    sourceUrl=receipt['url'],receiptSha256=receipt['sha256'],mainLine=True))
    return output

def collect(max_events,max_credits,root=ROOT,fetch=requests.get):
    if not 1<=max_events<=60 or not 5<=max_credits<=300:raise ValueError('Bounded collection required')
    key=os.environ.get('THE_ODDS_API_KEY') or (root/'data/.odds_key').read_text().strip()
    folder=root/'data/features/player_props/consensus'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');folder.mkdir(parents=True,exist_ok=True)
    receipts=[];events=[];rows=[];used=0;errors=[]
    def get(label,url,params):
        safe=url+('?' + urlencode(params) if params else '')
        rec=dict(source=label,url=safe,requestedAt=stamp())
        payload=None
        try:
            response=fetch(url,params={**params,'apiKey':key},timeout=20,allow_redirects=False)
            raw=response.content.replace(key.encode(),b'[REDACTED]');(folder/(label+'.json')).write_bytes(raw)
            rec.update(status=response.status_code,receivedAt=stamp(),sha256=hashlib.sha256(raw).hexdigest(),creditsUsed=int(response.headers.get('x-requests-last','0')),creditsRemaining=response.headers.get('x-requests-remaining'))
            if response.ok:payload=response.json()
        except (requests.RequestException,ValueError) as e:rec['error']=type(e).__name__
        receipts.append(rec);(folder/'receipts.json').write_text(json.dumps(receipts,indent=2)+'\n')
        return payload,rec
    status_file=root/'web/player-consensus-status.json'
    def status(ok,reason):
        report=dict(schemaVersion=1,lastAttemptAt=stamp(),success=ok,message=reason,creditsUsed=used)
        status_file.parent.mkdir(parents=True,exist_ok=True)
        temporary=status_file.with_suffix('.json.tmp');temporary.write_text(json.dumps(report,indent=2)+'\n');temporary.replace(status_file)
    def fail(reason):
        status(False,reason);raise RuntimeError(reason+'; receipts saved and previous snapshot preserved')
    sports,quota=get('quota',BASE+'/sports/',{})
    if not isinstance(sports,list) or int(quota.get('creditsRemaining') or 0)<max_credits:fail('Existing quota unavailable or insufficient')
    upcoming=[];sports_checked=[]
    for sport in SPORTS:
        if not any(s.get('key')==sport and s.get('active') for s in sports):continue
        inventory,rec=get(sport+'-events',BASE+'/sports/'+sport+'/events',{})
        if not isinstance(inventory,list):fail('Event discovery unavailable')
        sports_checked.append(sport)
        upcoming.extend(e for e in inventory if e.get('sport_key')==sport and datetime.fromisoformat(e['commence_time'].replace('Z','+00:00'))>datetime.now(timezone.utc))
    if not sports_checked:fail('NBA sport discovery unavailable')
    upcoming.sort(key=lambda e:(e['commence_time'],e['id']))
    # Prefer imminent games within the approved per-scan cap; explicitly report the window.
    # Unchecked distant games are excluded, never labeled as freshly collected.
    for event in upcoming[:max_events]:
        if used+len(MARKETS)>max_credits:fail('Collection credit limit reached before full scan')
        payload,rec=get('event-'+event['id'],BASE+'/sports/'+event['sport_key']+'/events/'+event['id']+'/odds',dict(bookmakers=','.join(BOOKS),markets=','.join(MARKETS),oddsFormat='american'))
        used+=rec.get('creditsUsed',len(MARKETS))
        if payload is None:
            errors.append({'eventId':event['id'],'status':rec.get('status'),'error':rec.get('error')});break # no repeated failed requests
        if not isinstance(payload,dict) or any(payload.get(k)!=event.get(k) for k in ('id','sport_key','home_team','away_team','commence_time')) or not isinstance(payload.get('bookmakers'),list):fail('Mismatched or malformed event response')
        event_rows=normalize(payload,rec);rows.extend(event_rows)
        events.append({**event,'competition':SPORTS[event['sport_key']],'checkedAt':rec['receivedAt'],'quoteCount':len(event_rows),'providers':sorted({r['provider'] for r in event_rows})})
    if errors:fail('Collection stopped after source failure')
    out=dict(schemaVersion=1,generatedAt=stamp(),scope='game',season='2026-27',events=events,quotes=rows,
        coverage=dict(eventsDiscovered=len(upcoming),eventsChecked=len(events),complete=len(events)==len(upcoming),eventsOutsideWindow=max(0,len(upcoming)-max_events),selectionPolicy='Earliest upcoming games, up to '+str(max_events)+' per scan',providers=list(BOOKS),stats=list(MARKETS.values()),creditsUsed=used,sports=sports_checked,playersWithLines=len({r['player'] for r in rows}),eventsWithLines=sum(e['quoteCount']>0 for e in events),providerQuoteCounts={b:sum(r['provider']==b for r in rows) for b in BOOKS}),
        refreshPolicy=('Scheduled collection every six hours. ' if os.environ.get('NBAI_SCHEDULED_COLLECTION')=='1' else 'Scheduled collection is awaiting setup. ')+'The open page checks for new snapshots every five minutes. Quotes are not live.',
        source='The Odds API',documentation='https://the-odds-api.com/sports-odds-data/bookmaker-apis.html',
        seasonAverageCoverage='Unverified: this feed provides single-game lines, not season-average markets.',
        note='Saved snapshot, not a live feed. Main lines only. DFS synthetic odds are excluded. Cross-app median is a descriptive reference; settlement and payout rules differ.')
    (folder/'normalized.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
    dest=root/'web/player-consensus.json';temporary=dest.with_suffix('.json.tmp');temporary.write_text(json.dumps(out,indent=2,allow_nan=False)+'\n');temporary.replace(dest)
    status(True,'Complete scan published')
    print(json.dumps({'playersWithLines':out['coverage']['playersWithLines'],'providerQuoteCounts':out['coverage']['providerQuoteCounts'],'events':len(events),'quotes':len(rows),'creditsUsed':used,'runDir':str(folder)}))
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--collect',action='store_true');p.add_argument('--max-events',type=int,default=60);p.add_argument('--max-credits',type=int,default=300);a=p.parse_args()
    if not a.collect:p.error('Pass --collect to use existing API credits')
    collect(a.max_events,a.max_credits)
