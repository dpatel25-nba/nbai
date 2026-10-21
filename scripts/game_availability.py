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
    # ESPN occasionally omits signed two-way players. Keep the original snapshot
    # immutable and confirm any strict-subset addition against a fresh NBA page.
    # Membership evidence never supplies health or a starting-role assertion.
    from current_roster_reconciliation import reconcile
    canonical_path = P.ROOT/'web/season-data.js'
    canonical_bytes = canonical_path.read_bytes()
    data = json.loads(canonical_bytes.decode().split(' = ', 1)[1].rstrip(';\n'))
    canonical = dict(season=data['season'], teams=data['teams'], players={
        key: {field: player[field] for field in ['id', 'n', 'team', 'rookie', 'rateProvenance'] if field in player}
        for key, player in data['players'].items()})
    original = run/'rosters.parquet'
    if not original.exists():
        rosters.to_parquet(original, index=False)
    else:
        # Bind the original pin to the actual rows being reconciled, including
        # all identity and metadata columns. Ordering and parquet dtype changes
        # do not change row meaning, but changed values must fail closed.
        ordering = ['TEAM', 'PLAYER_ID', 'ESPN_ID']
        stored = pd.read_parquet(original)
        try:
            pd.testing.assert_frame_equal(
                stored.sort_values(ordering).reset_index(drop=True).sort_index(axis=1),
                rosters.sort_values(ordering).reset_index(drop=True).sort_index(axis=1),
                check_dtype=False, check_exact=True)
        except (AssertionError, KeyError, TypeError) as exc:
            raise ValueError('Original roster pin does not match supplied roster rows') from exc
    blocked = {}
    by_event = {e['EVENT_ID']: e for e in events}
    for exclusion in exclusions:
        event = by_event[exclusion['event']]
        for team in [event['HOME_TEAM'], event['AWAY_TEAM']]:
            blocked.setdefault(team, []).append(exclusion['reason'])
    raw_dir = run/'raw'/'roster_reconciliation'
    raw_dir.mkdir(parents=True, exist_ok=True)
    P.write_json(raw_dir/'canonical-roster.json', canonical)
    effective, audit = reconcile(rosters, canonical, raw_dir, unresolved=blocked)
    if canonical_path.read_bytes() != canonical_bytes:
        raise ValueError('Canonical roster changed during availability reconciliation')
    audit['originalRoster'] = {'path': str(original.relative_to(P.ROOT)), 'sha256': P.sha(original)}
    audit['canonicalWebsiteSha256'] = P.sha(canonical_path)
    audit['sourceHashes'] = {str((P.ROOT/'scripts'/name).relative_to(P.ROOT)): P.sha(P.ROOT/'scripts'/name)
                           for name in ['game_availability.py', 'current_roster_reconciliation.py']}
    statuses = dict(statuses)
    for pid in audit['appendedPlayerIds']:
        statuses[pid] = dict(status='Unknown', out=False)
    exclusions = list(exclusions)
    for event in events:
        if {event['HOME_TEAM'], event['AWAY_TEAM']}.intersection(audit['heldTeams']):
            exclusions.append(dict(event=event['EVENT_ID'], reason='Current roster reconciliation needs review'))
    if audit['appendedPlayerIds']:
        effective_path = run/'current_roster'/'rosters.parquet'
        effective_path.parent.mkdir(parents=True, exist_ok=True)
        effective.to_parquet(effective_path, index=False)
    else:
        effective_path = original
    audit['effectiveRoster'] = {'path': str(effective_path.relative_to(P.ROOT)), 'sha256': P.sha(effective_path)}
    P.write_json(run/'roster-reconciliation.json', audit)
    files = [p for p in run.rglob('*') if p.is_file() and
             (p.suffix == '.parquet' or (run/'raw') in p.parents or p.name in ['roster_cache_reference.json', 'roster-reconciliation.json'])
             and not (p == original and effective_path != original)]
    result = build(events, effective, statuses, exclusions, roster_at, injury_at, provider_at,
        {str(p.relative_to(P.ROOT)): P.sha(p) for p in files})
    result['rosterReconciliation'] = dict(appendedPlayerIds=audit['appendedPlayerIds'],
        heldTeams=audit['heldTeams'], sourcePath=str((run/'roster-reconciliation.json').relative_to(P.ROOT)),
        meaning='Fresh NBA roster confirmation supplies membership only. NBA-only players retain Unknown availability and cannot become recommended props.')
    if audit['appendedPlayerIds']:
        result['source'] = 'ESPN rosters/injuries with independently confirmed NBA roster additions'
    P.write_json(run/'game-availability.json', result)
    destination = P.ROOT/'web/game-availability.json'
    temporary = destination.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    temporary.replace(destination)
    return str(run/'game-availability.json')
