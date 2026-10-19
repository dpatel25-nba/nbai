"""Export current, matched availability observations for upcoming game scenarios."""
import json
from datetime import timedelta
from pathlib import Path

import pandas as pd
import shadow_pipeline as P


def eligible_events(events,roster_issues,unresolved_injuries):
    """Keep unrelated games; unknown injury teams conservatively hold all games."""
    injury_teams={r.get('team_code') for r in unresolved_injuries}
    safe=[];excluded=[]
    for event in events:
        sides={event['HOME_TEAM'],event['AWAY_TEAM']}
        reasons=[]
        if sides.intersection(roster_issues):reasons.append('incomplete current roster')
        if None in injury_teams or sides.intersection(injury_teams):
            reasons.append('unresolved injury identities')
        if reasons:excluded.append(dict(event=event['EVENT_ID'],reason='; '.join(reasons)))
        else:safe.append(event)
    return safe,excluded


def build(events, rosters, statuses, exclusions, roster_at, injury_at, provider_at, sources, now=None):
    now = now or P.utcnow()
    times = [pd.Timestamp(roster_at), pd.Timestamp(injury_at)]
    if provider_at:
        provider = pd.Timestamp(provider_at)
        if provider.tzinfo is None or not 0 <= (pd.Timestamp(now)-provider).total_seconds() <= 86400:
            raise ValueError('Injury provider observation is stale or future dated')
    if any(t.tzinfo is None or not 0 <= (pd.Timestamp(now)-t).total_seconds() <= 21600 for t in times):
        raise ValueError('Current roster and injury observations must be at most six hours old')
    expires = min(times) + timedelta(hours=6)
    if provider_at:expires = min(expires, provider + timedelta(hours=24))
    held = {e['event']: e['reason'] for e in exclusions}
    result = []
    for e in events:
        rows = rosters[rosters.TEAM.isin([e['HOME_TEAM'], e['AWAY_TEAM']])]
        reason = held.get(e['EVENT_ID'])
        if set(rows.SEASON) != {e['SEASON']}:
            reason = 'Current roster season does not match this game'
        result.append(dict(eventId=e['EVENT_ID'], home=e['HOME_TEAM'], away=e['AWAY_TEAM'],
            startsAt=e['TIPOFF_UTC'], season=e['SEASON'], holdReason=reason,
            players=[dict(id=int(r.PLAYER_ID), name=r.PLAYER, team=r.TEAM,
                status=statuses.get(r.PLAYER_ID, {}).get('status', 'Not listed'),
                out=statuses.get(r.PLAYER_ID, {}).get('out', False)) for r in rows.itertuples()]))
    return dict(schemaVersion=1, generatedAt=now.isoformat(), rosterObservedAt=roster_at,
        injuryObservedAt=injury_at, providerUpdatedAt=provider_at, validUntil=expires.isoformat(),
        source='ESPN current rosters and injury feed', sourceUrl=P.BASE+'/injuries',
        sourceHashes=sources, events=result,
        assumptions='Reported Out players are excluded. Other players are conditional on playing; questionable participation, confirmed starters and minutes restrictions are not modeled. Not listed does not certify health.')


def export(run, events, rosters, statuses, exclusions, roster_at, injury_at, provider_at):
    run = Path(run)
    files = [p for p in run.rglob('*') if p.is_file() and
             (p.suffix == '.parquet' or p.parent.name == 'raw' or p.name == 'roster_cache_reference.json')]
    result = build(events, rosters, statuses, exclusions, roster_at, injury_at, provider_at,
        {str(p.relative_to(P.ROOT)): P.sha(p) for p in files})
    P.write_json(run/'game-availability.json', result)
    destination = P.ROOT/'web/game-availability.json'
    temporary = destination.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    temporary.replace(destination)
    return str(run/'game-availability.json')
