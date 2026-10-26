"""Build a transparent daily statistical research card from public observations.

No orders, paid APIs, fitted probabilities, fabricated news or guaranteed picks.
All source responses and previous/publication bytes are archived per run. Closed
games are immutable. Canonical model fitting and historical tables are untouched.
"""
import argparse
from collections import Counter
from datetime import datetime,timedelta,timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import unicodedata
import urllib.request
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
BASE='https://site.api.espn.com/apis/site/v2/sports/basketball/nba'
ET=ZoneInfo('America/New_York')
ALIASES={'NY':'NYK','SA':'SAS','GS':'GSW','PHO':'PHX','NO':'NOP','UTAH':'UTA','WSH':'WAS'}
STATS={'PTS':'points','REB':'rebounds','AST':'assists','FG3M':'threes'}
VERSION='daily-statistical-research-v1'
POLICY={'minimumSeasonAppearances':25,'minimumAsk':.20,'maximumAsk':.65,'minimumDisplayedSize':5,
    'historicalRateBuffer':.10,'minimumRecentMinutes':20,'minimumWorkloadRatio':.75,'maximumWorkloadRatio':1.35,
    'minimumRecentOpportunityRatio':.80,'maximumPicksPerGame':2,'maximumPicksPerDay':12,'reviewHours':3,
    'minimumThresholds':{'PTS':9.5,'REB':3.5,'AST':2.5,'FG3M':1.5}}

def clock(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('timestamp lacks timezone')
    return d.astimezone(timezone.utc)

def norm(value):
    return ''.join(c for c in unicodedata.normalize('NFD',str(value)).lower() if c.isalnum() and not unicodedata.combining(c))

def require(condition, reason):
    if not condition:raise ValueError(reason)

def save(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def official_report(run,now):
    from historical_sources import parse_historical_report
    slot=now.astimezone(ET).replace(second=0,microsecond=0)
    slot=slot.replace(minute=(slot.minute//15)*15)
    attempts=[]
    for offset in range(7):
        at=slot-timedelta(minutes=15*offset)
        url='https://ak-static.cms.nba.com/referee/injury/Injury-Report_'+at.strftime('%Y-%m-%d_%I_%M%p')+'.pdf'
        try:
            with urllib.request.urlopen(url,timeout=15) as response:
                raw=response.read();require(response.status==200 and raw.startswith(b'%PDF'),'invalid official report response')
            path=run/(at.strftime('official-%H%M')+'.pdf');path.write_bytes(raw)
            stamp,rows,submissions=parse_historical_report(path)
            require(stamp.to_pydatetime()==at and timedelta(0)<=now-at<=timedelta(minutes=90),'official report timestamp mismatch')
            for row in rows+submissions:
                row['date']=datetime.strptime(row['date'],'%m/%d/%Y').date().isoformat()
            result={'url':url,'reportedAt':at.isoformat(),'observedAt':datetime.now(timezone.utc).isoformat(),
                'sha256':digest(path),'rows':rows,'submissions':submissions,'attempts':attempts}
            save(run/'official-report.json',result);return result
        except Exception as exc:attempts.append({'url':url,'error':type(exc).__name__+': '+str(exc)[:150]})
    save(run/'official-report-failure.json',{'attempts':attempts})
    raise ValueError('No fresh parseable official injury report; publication withheld')

def person_key(name):
    return norm(re.sub(r'\s+(?:Jr\.?|II|III|IV)$','',name,flags=re.I))

def official_game_hold(official,date,away,home,home_name,away_name):
    entries=[r for r in official['submissions'] if r['date']==date and r['matchup']==away+'@'+home]
    for name in [home_name,away_name]:
        matches=[r for r in entries if norm(r['team'])==norm(name)]
        if len(matches)!=1 or matches[0]['submitted'] is not True:return 'Official team report missing or not yet submitted: '+name
    return None

def fetch(run,name,url):
    requested=datetime.now(timezone.utc).isoformat()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'NBAI research/1.0'}),timeout=25) as response:
                raw=response.read();require(response.status==200,'source HTTP failure')
            value=json.loads(raw);break
        except Exception:
            if attempt==2:raise
            time.sleep(1+attempt)
    path=run/(name+'.json');path.write_bytes(raw)
    save(run/(name+'.receipt.json'),{'url':url,'requestedAt':requested,'observedAt':datetime.now(timezone.utc).isoformat(),'sha256':digest(path)})
    return value

def teams(event):
    return {p['homeAway']:ALIASES.get(p['team']['abbreviation'],p['team']['abbreviation']) for p in event['competitions'][0]['competitors']}

def parse_box(payload):
    result={}
    for team in payload.get('boxscore',{}).get('players',[]):
        code=ALIASES.get(team['team']['abbreviation'],team['team']['abbreviation'])
        rows={}
        for group in team.get('statistics',[]):
            labels=group['labels']
            for player in group.get('athletes',[]):
                name=norm(player['athlete']['displayName'])
                if name in rows:raise ValueError('ambiguous box player')
                stats=dict(zip(labels,player.get('stats',[])))
                if player.get('didNotPlay') is True:
                    rows[name]={'minutes':0.,'played':False};continue
                try:
                    parts=str(stats['MIN']).split(':');minutes=float(parts[0])+(float(parts[1])/60 if len(parts)==2 else 0)
                    values={'PTS':float(stats['PTS']),'REB':float(stats['REB']),'AST':float(stats['AST']),'FG3M':float(stats['3PT'].split('-')[0]),
                        'FGA':float(stats['FG'].split('-')[1]),'FG3A':float(stats['3PT'].split('-')[1])}
                    require(math.isfinite(minutes) and 0<minutes<=80 and all(math.isfinite(v) and v>=0 for v in values.values()),'invalid box values')
                    rows[name]={'minutes':minutes,'played':True,'stats':values,'starter':bool(player.get('starter'))}
                except (ValueError,KeyError,IndexError):rows[name]={'minutes':None,'played':None}
        result[code]=rows
    return result

def candidate(market,player,history,recent,status,now):
    """Pure, fixed screen. Historical hit fractions are NOT win probabilities."""
    stat=market.get('stat');threshold=market.get('threshold');ask=market.get('yesAsk')
    if stat not in STATS or not isinstance(threshold,(int,float)) or not math.isfinite(threshold) or threshold<POLICY['minimumThresholds'][stat]:return None
    if status not in ['Not listed','Available','Active','Probable']:return None
    if not market.get('quoteValid') or not isinstance(ask,(float,int)) or not POLICY['minimumAsk']<=ask<=POLICY['maximumAsk']:return None
    if (market.get('yesSize') or 0)<POLICY['minimumDisplayedSize']:return None
    if not -1 <= (now-clock(market['observedAt'])).total_seconds() <= 900:return None
    if len(recent)<1 or not recent[0].get('played'):return None
    # A recent DNP/unknown or a material workload change needs individual review.
    if any(not r.get('played') for r in recent):return None
    minutes=sum(r['minutes'] for r in recent)/len(recent)
    if recent[0]['minutes']<POLICY['minimumRecentMinutes'] or minutes<POLICY['minimumRecentMinutes']:return None
    hist=[]
    for year in ['2024-25','2025-26']:
        h=history.get(year,{});s=h.get('stats',{}).get(stat,{})
        n=s.get('n',0)
        if n<POLICY['minimumSeasonAppearances']:return None
        hits=sum(v for k,v in s['counts'].items() if float(k)>threshold)
        hist.append({'season':year,'hits':hits,'n':n,'meanMinutes':h['meanMinutes']})
    ratio=minutes/hist[-1]['meanMinutes']
    if not POLICY['minimumWorkloadRatio']<=ratio<=POLICY['maximumWorkloadRatio']:return None
    opportunity={'PTS':'FGA','FG3M':'FG3A'}.get(stat,stat)
    old=history['2025-26'].get('stats',{}).get(opportunity,{})
    if not old.get('n') or any(opportunity not in r.get('stats',{}) for r in recent):return None
    prior_opportunity=sum(float(k)*v for k,v in old['counts'].items())/old['n']
    recent_opportunity=sum(r['stats'][opportunity] for r in recent)/len(recent)
    if prior_opportunity<=0 or recent_opportunity<prior_opportunity*POLICY['minimumRecentOpportunityRatio']:return None
    limit=math.floor((min(h['hits']/h['n'] for h in hist)-POLICY['historicalRateBuffer']+1e-9)*100)/100
    limit=min(POLICY['maximumAsk'],limit)
    if ask>limit:return None
    return {'history':hist,'recent':recent,'recentMinutes':minutes,'maxPrice':limit,'screenMargin':limit-ask,
        'opportunity':{'stat':opportunity,'recent':recent_opportunity,'historical':prior_opportunity}}

def merge_reviews(previous, reviews, now):
    """Refresh upcoming reviewed games only; preserve every closed record."""
    incoming={r['gameId']:r for r in reviews};result=[];seen=set()
    for old in previous:
        gid=old['gameId'];require(gid not in seen,'duplicate existing game review');seen.add(gid)
        if clock(old.get('officialStartsAt',old['startsAt']))<=now or gid not in incoming:
            result.append(old)
        else:result.append(incoming[gid])
    result.extend(r for gid,r in incoming.items() if gid not in seen)
    return result

def build(root,run,date,now,model,availability,quotes,history,today,latest,boxes,official):
    require(history['latestGameDate']<date,'historical data overlaps target day')
    require(clock(availability['generatedAt'])<=now+timedelta(seconds=1) and clock(availability['validUntil'])>now,'availability expired or future dated')
    require((now-clock(availability['generatedAt'])).total_seconds()<=21600,'availability stale')
    require(clock(model['validUntil'])>now,'model identity bundle expired')
    require(len({m['ticker'] for m in quotes['markets']})==len(quotes['markets']),'duplicate quotes')
    source={'title':'Current ESPN roster and injury observations','url':BASE+'/injuries'}
    historical_source={'title':'NBA traditional box-score archive · 2024–25 and 2025–26','url':'https://www.nba.com/stats/players/traditional'}
    reviews=[];candidates=[];excluded=Counter()
    for event in today['events']:
        sides=teams(event);tip=clock(event['date'])
        if tip.astimezone(ET).date().isoformat()!=date or tip<=now or event['status']['type']['state']!='pre':continue
        matches=[m for m in model['games'] if m['home']==sides['home'] and m['away']==sides['away'] and abs((clock(m.get('officialStartsAt',m['startsAt']))-tip).total_seconds())<=60]
        require(len(matches)==1,'upcoming game missing exact model/schedule identity')
        game=matches[0]
        av=[a for a in availability['events'] if a['home']==game['home'] and a['away']==game['away'] and abs((clock(a['startsAt'])-tip).total_seconds())<=60]
        require(len(av)==1,'missing unique availability game')
        av=av[0]
        review={'gameId':game['gameId'],'home':game['home'],'away':game['away'],'startsAt':game['startsAt'],
            'officialStartsAt':tip.isoformat(),'reviewedAt':now.isoformat(),'validUntil':min(tip,now+timedelta(hours=POLICY['reviewHours']),clock(availability['validUntil'])).isoformat(),
            'reviewType':'automated_statistics','modelEvidenceStatus':'rotation_review_required',
            'headline':'Daily statistical research · '+game['away']+' at '+game['home'],
            'analysis':'Automated screen of two archived seasons, the latest completed team games, current reported availability and observed Kalshi prices. Historical frequencies are not matchup probabilities. No independent winner edge is claimed.',
            'winnerTeam':None,'winnerReason':'No automatic winner recommendation; this screen evaluates player thresholds only.',
            'sources':[{'title':'Official NBA injury report · '+official['reportedAt'],'url':official['url']},source,{'title':'Today’s ESPN matchup','url':'https://www.espn.com/nba/game/_/gameId/'+event['id']}],
            'picks':[],'combo':{'status':'held','withdrawalReason':'Singles research only. No validated joint probability or executable combo price.'}}
        reviews.append(review)
        official_hold=official_game_hold(official,date,game['away'],game['home'],game['homeName'],game['awayName'])
        if official_hold:
            review['analysis']='Pass pending official availability: '+official_hold+'. No automatic picks for this matchup.';continue
        if av.get('holdReason'):
            review['analysis']='Pass pending roster/availability reconciliation: '+av['holdReason']+'. No automatic picks for this matchup.';continue
        statuses={p['id']:p for p in av['players']}
        for market in quotes['markets']:
            identity=market.get('identity',{})
            if identity.get('date')!=date or set(identity.get('teams',[]))!={game['home'],game['away']} or market.get('stat') not in STATS:continue
            people=[p for p in game['players'] if norm(p['name'])==norm(market.get('player'))]
            if len(people)!=1:excluded['unmatched_player']+=1;continue
            person=people[0];a=statuses.get(person['id'])
            if not a or a['team']!=person['team'] or a.get('out'):excluded['unavailable_player']+=1;continue
            team_name=game['homeName'] if person['team']==game['home'] else game['awayName']
            official_status=[r['status'] for r in official['rows'] if r['date']==date and r['matchup']==game['away']+'@'+game['home']
                and norm(r['team'])==norm(team_name) and person_key(r['player'])==person_key(person['name'])]
            if len(official_status)>1 or any(s not in ['Available','Probable'] for s in official_status):excluded['official_status_hold']+=1;continue
            recent=[]
            for past in latest.get(person['team'],[])[:2]:
                row=boxes.get(past['id'],{}).get(person['team'],{}).get(norm(person['name']))
                if row is None:recent.append({'played':None});continue
                recent.append({**row,'eventId':past['id'],'date':past['date'],'url':'https://www.espn.com/nba/boxscore/_/gameId/'+past['id']})
            result=candidate(market,person,history['players'].get(str(person['id']),{}),recent,a['status'],now)
            if not result:excluded['screen_not_met']+=1;continue
            result.update(game=game,review=review,market=market,person=person,status=a['status'])
            candidates.append(result)
    candidates.sort(key=lambda r:(-r['screenMargin'],-min(h['n'] for h in r['history']),r['market']['ticker']))
    counts=Counter();used=set();selected=[]
    # Round-robin by game: breadth before a second prop in the same matchup.
    for round_number in range(POLICY['maximumPicksPerGame']):
        for r in candidates:
            game,person,market=r['game'],r['person'],r['market'];gid=game['gameId']
            if counts[gid]!=round_number or person['id'] in used or len(selected)>=POLICY['maximumPicksPerDay']:continue
            counts[gid]+=1;used.add(person['id']);selected.append(r)
    for priority,r in enumerate(selected,1):
        market,person=r['market'],r['person'];stat=market['stat'];recent=r['recent'];h=r['history']
        label=f"{person['name']} {math.floor(market['threshold'])+1}+ {STATS[stat]}"
        evidence=[f"{x['season']}: {x['hits']}/{x['n']} valid positive-minute appearances reached this threshold; includes prior teams." for x in h]
        evidence += [f"Latest completed team game: {x['minutes']:g} minutes and {x['stats'][stat]:g} {STATS[stat]} on {x['date'][:10]}." for x in recent]
        evidence += [f"Current provider status: {r['status']}. Not listed does not confirm health or a starting role.",
            f"Recent {r['opportunity']['stat']} average {r['opportunity']['recent']:.1f}, versus {r['opportunity']['historical']:.1f} in 2025–26. The fixed screen requires at least 80% of that historical opportunity/count level.",
            'Price ceiling is the lower of the two historical hit fractions minus 10 percentage points, capped at 65¢; this is a fixed screening rule before fees, not a fair-value estimate.',
            'Automatically generated from source data; no human news interpretation or model-certified edge. Recheck lineup, workload and actual fees before any entry.']
        pick={'status':'reviewed','reviewType':'automated_statistics','ticker':market['ticker'],'playerId':person['id'],
            'player':person['name'],'team':person['team'],'stat':stat,'threshold':market['threshold'],'side':'yes',
            'priority':priority,'label':label,'contractTitle':market['title'],'maxPrice':r['maxPrice'],
            'conviction':'Automated statistical research lean','basis':'fundamental','modelConfirmed':False,
            'thesis':f"Both archived seasons support investigating this threshold: {h[0]['hits']}/{h[0]['n']} and {h[1]['hits']}/{h[1]['n']} hits. The last {len(recent)} completed team game(s) show {r['recentMinutes']:.1f} average minutes; the observed offer passes the fixed historical-price screen.",
            'risk':'Older roles and opponents may differ from today. Minutes, foul trouble, shooting variance and late injury news can invalidate the comparison. This screen does not establish positive expected return.',
            'history':h,'quoteAtReview':{'price':market['yesAsk'],'size':market['yesSize'],'observedAt':market['observedAt']},
            'evidence':evidence,'sources':[{'title':'Official NBA injury report · '+official['reportedAt'],'url':official['url']},source,historical_source,*[{'title':'Recent completed game box score','url':x['url']} for x in recent]],
            'withinPriceLimit':True,'executionVerified':False}
        r['review']['picks'].append(pick)
    for review in reviews:
        if not review['picks'] and 'Pass pending' not in review['analysis']:
            review['analysis']+=' No player threshold passed every availability, workload, sample-size and price rule at this scan; pass for now.'
    return reviews,dict(eligibleCandidates=len(candidates),selected=len(selected),excluded=dict(excluded))

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--date');parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args();root=args.root.resolve();now=datetime.now(timezone.utc)
    date=args.date or now.astimezone(ET).date().isoformat()
    require(date==now.astimezone(ET).date().isoformat(),'only today may be published by the daily job')
    run=root/'data/features/daily_research_publication'/date/now.strftime('%H%M%S%fZ');run.mkdir(parents=True)
    target=root/'web/research-picks.json';previous=json.loads(target.read_text());(run/'previous-research.json').write_bytes(target.read_bytes())
    model=json.loads((root/'web/kalshi-board-model.json').read_text());availability=json.loads((root/'web/game-availability.json').read_text())
    history=json.loads((root/'web/daily-pick-history.json').read_text())
    today=fetch(run,'schedule',BASE+'/scoreboard?dates='+date.replace('-',''))
    # Empty slates and completed dates are successful no-ops, never recycled picks.
    pending=[e for e in today['events'] if clock(e['date'])>now and e['status']['type']['state']=='pre']
    if not pending:save(run/'report.json',{'status':'no_upcoming_games','dateEt':date});print('No upcoming games; archived reviews retained.');return
    official=official_report(run,now)
    latest={};all_past={}
    for offset in range(1,8):
        day=(now.astimezone(ET).date()-timedelta(days=offset)).strftime('%Y%m%d')
        past=fetch(run,'schedule-'+day,BASE+'/scoreboard?dates='+day)
        for event in past['events']:
            if event['status']['type']['state']!='post':continue
            all_past[event['id']]=event
            for code in teams(event).values():latest.setdefault(code,[]).append(event)
    wanted={t for e in pending for t in teams(e).values()}
    for code in latest:latest[code].sort(key=lambda e:e['date'],reverse=True)
    ids=sorted({e['id'] for code in wanted for e in latest.get(code,[])[:2]})
    boxes={eid:parse_box(fetch(run,'box-'+eid,BASE+'/summary?event='+eid)) for eid in ids}
    quote_path=run/'quotes.json'
    js="const fs=require('fs');require('./web/lib/kalshi-board.cjs').collect().then(q=>fs.writeFileSync(process.argv[1],JSON.stringify(q))).catch(e=>{console.error(e.message);process.exit(1)});"
    subprocess.run([os.environ.get('NBAI_NODE_BIN','node'),'-e',js,str(quote_path)],cwd=root,check=True,timeout=180)
    quotes=json.loads(quote_path.read_text());now=datetime.now(timezone.utc)
    reviews,summary=build(root,run,date,now,model,availability,quotes,history,today,latest,boxes,official)
    result={**previous,'generatedAt':now.isoformat(),'automation':{'version':VERSION,'dateEt':date,'policy':POLICY,
        'mode':'Automated statistical research; no human editorial review or demonstrated betting edge.'},
        'method':'Daily automatic research screen: two historical seasons, recent completed-game workloads, fresh availability and current Kalshi offers. Explicit passes when evidence is insufficient.',
        'modelSnapshot':{'generatedAt':model['generatedAt'],'status':'rotation_review_required'},
        'games':merge_reviews(previous['games'],reviews,now)}
    save(run/'published-research.json',result)
    save(run/'report.json',{'status':'prepared','dateEt':date,**summary,'games':len(reviews),'policyVersion':VERSION,
        'inputs':{p.name:digest(p) for p in [root/'web/kalshi-board-model.json',root/'web/game-availability.json',root/'web/daily-pick-history.json',quote_path,Path(__file__)]},
        'outputSha256':digest(run/'published-research.json')})
    # All collection and validation finish before any public asset is replaced.
    temporary=target.with_suffix('.json.tmp');temporary.write_bytes((run/'published-research.json').read_bytes());temporary.replace(target)
    (root/'web/kalshi-board-quotes.json').write_bytes(quote_path.read_bytes())
    print(json.dumps({'dateEt':date,'games':len(reviews),**summary,'archive':str(run)}))

if __name__=='__main__':main()
