"""Refresh the website's current availability using public feeds; no fitting or paid API."""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path
import uuid

import pandas as pd
import shadow_pipeline as P
from game_availability import eligible_events, export


def collect():
    data=json.loads((P.ROOT/'web/season-data.js').read_text().split(' = ',1)[1].rstrip(';\n'))
    teams={t:v['id'] for t,v in data['teams'].items()}
    now=P.utcnow();day=pd.Timestamp(now).tz_convert('America/New_York').date()
    run=P.ROOT/'data/features/game_availability'/uuid.uuid4().hex
    run.mkdir(parents=True)
    events=[]
    for date in [day,day+timedelta(days=1)]:
        payload,_=P.fetch_json(f'{P.BASE}/scoreboard?dates={date:%Y%m%d}',run/'raw'/f'schedule_{date}.json')
        events.extend(P.parse_schedule(payload,teams))
    events=[e for e in events if e['STATE']=='pre' and pd.Timestamp(e['TIPOFF_UTC'])>pd.Timestamp(now)]
    events=list({e['EVENT_ID']:e for e in events}.values())
    names={};meta={}
    for p in data['players'].values():
        names.setdefault(P.normalize(p['n']),set()).add(p['id'])
        meta[p['id']]={'IS_ROOKIE':p['rookie']}
    for alias in json.loads((P.ROOT/'scripts/player_identity_overrides.json').read_text()):
        if str(alias['nba_id']) in data['players']:
            names.setdefault(P.normalize(alias['name']),set()).add(alias['nba_id'])
    wanted=sorted({t for e in events for t in [e['HOME_TEAM'],e['AWAY_TEAM']]})
    reverse={v:k for k,v in P.ALIASES.items()}
    def one(t):
        payload,stamp=P.fetch_json(f'{P.BASE}/teams/{reverse.get(t,t).lower()}/roster',run/'raw'/f'roster_{t}.json')
        rows,bad=P.parse_roster(payload,teams[t],t,names,meta)
        return rows,bad,stamp
    rows=[];issues={};stamps=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(one,t):t for t in wanted}
        for future in as_completed(futures):
            team=futures[future]
            try:
                parsed,bad,stamp=future.result();rows.extend(parsed);stamps.append(stamp)
                if bad:issues[team]=bad
            except Exception as exc:issues[team]=[{'error':str(exc)}]
    rosters=pd.DataFrame(rows,columns=['PLAYER_ID','ESPN_ID','PLAYER','TEAM_ID','TEAM','SEASON','IS_ROOKIE','DRAFT_SLOT'])
    if rosters.PLAYER_ID.duplicated().any():raise ValueError('Duplicate current roster identity')
    injuries,injury_at=P.fetch_json(P.BASE+'/injuries',run/'raw/injuries.json')
    statuses,unmatched=P.parse_injuries(injuries,rosters)
    # Unrequested teams have no roster in this bounded capture. Keep unknown
    # team names global, but don't label known off-slate teams as missing data.
    unmatched=[u for u in unmatched if u['team_code'] is None or u['team_code'] in wanted]
    _,excluded=eligible_events(events,issues,unmatched)
    output=export(run,events,rosters,statuses,excluded,min(stamps) if stamps else now.isoformat(),
                  injury_at,injuries.get('timestamp'))
    P.write_json(run/'manifest.json',dict(events=len(events),excluded=excluded,rosterIssues=issues,
        unresolvedInjuries=unmatched,output=output,websiteRosterSha256=P.sha(P.ROOT/'web/season-data.js')))
    print(json.dumps(dict(events=len(events),held=len(excluded),run=str(run))))


if __name__=='__main__':collect()
