"""Bounded NBA confirmation of ESPN roster omissions; never availability evidence.

The caller retains unresolved ESPN identities and applies ``heldTeams`` to its
game holds. Only nonempty strict subsets can be augmented. Every augmentation
requires a new HTTP observation; this module does not reuse archived responses.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _clock():
    return datetime.now(timezone.utc).isoformat()


def _id(value):
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise ValueError('invalid integer identity')
    return int(value)


def _save(path, body):
    with path.open('xb') as stream:
        stream.write(body)
    return dict(path=str(path.resolve()), sha256=_sha(body), bytes=len(body))


def _official(body, team_id, season):
    match = re.search(r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
                      body.decode('utf-8'), flags=re.S)
    if not match:
        raise ValueError('official embedded roster missing')
    team = json.loads(match[1])['props']['pageProps']['team']
    if _id(team['id']) != team_id:
        raise ValueError('official team mismatch')
    rows = team['roster']
    if not isinstance(rows, list) or not 5 <= len(rows) <= 30:
        raise ValueError('official roster size invalid')
    parsed = {}
    for row in rows:
        pid = _id(row['PLAYER_ID'])
        if pid in parsed:
            raise ValueError('official duplicate player')
        if _id(row['TeamID']) != team_id or str(row['SEASON']) != season[:4]:
            raise ValueError('official season or team mismatch')
        if not isinstance(row['PLAYER'], str) or not row['PLAYER'].strip():
            raise ValueError('official name missing')
        parsed[pid] = row['PLAYER']
    return parsed


def reconcile(rosters, canonical, archive_dir, *, unresolved=None, fetch=None):
    """Return (effective dataframe, audit); held teams are NEVER repaired silently.

    ``fetch`` is a requests.get-compatible injectable transport returning a
    response with content/status_code/url/headers. It is for fixtures, not cached
    evidence. The production default performs a new official NBA GET.
    """
    required = {'PLAYER_ID', 'ESPN_ID', 'PLAYER', 'TEAM_ID', 'TEAM', 'SEASON'}
    if not required <= set(rosters.columns):
        raise ValueError('roster schema incomplete')
    season = canonical['season']
    if not re.fullmatch(r'20\d\d-\d\d', season) or int(season[-2:]) != (int(season[:4])+1) % 100:
        raise ValueError('canonical season invalid')
    players = {}
    for key, player in canonical['players'].items():
        pid = _id(player['id'])
        if _id(key) != pid or pid in players or player['team'] not in canonical['teams']:
            raise ValueError('canonical identity invalid')
        if not isinstance(player['n'], str) or not player['n'].strip():
            raise ValueError('canonical name missing')
        players[pid] = player
    out = rosters.copy(deep=True)
    unresolved = unresolved or {}
    audit = dict(schemaVersion=1, startedAt=_clock(), season=season,
                 membershipOnly=True, certifiesHealth=False, certifiesStartingRole=False,
                 canonicalSha256=_sha(json.dumps(canonical, sort_keys=True, separators=(',', ':'),
                                                  allow_nan=False).encode()),
                 inputRows=len(rosters), teams={}, appendedPlayerIds=[], heldTeams=[])
    transport = fetch or requests.get
    directory = Path(archive_dir)
    directory.mkdir(parents=True, exist_ok=True)
    duplicate_ids = set(rosters.loc[rosters.PLAYER_ID.duplicated(keep=False), 'PLAYER_ID'])
    duplicate_espn = set(rosters.loc[rosters.ESPN_ID.duplicated(keep=False), 'ESPN_ID'])
    for code in sorted(set(rosters.TEAM) | set(unresolved)):
        detail = dict(appendedPlayerIds=[], missingPlayerIds=[], status='held')
        audit['teams'][code] = detail
        try:
            if code not in canonical['teams']:
                raise ValueError('unknown team')
            meta = canonical['teams'][code]
            tid = _id(meta['id'])
            expected = {pid: p for pid, p in players.items() if p['team'] == code}
            rows = rosters[rosters.TEAM == code]
            actual = {_id(p) for p in rows.PLAYER_ID}
            detail.update(expectedPlayerIds=sorted(expected), observedPlayerIds=sorted(actual),
                          missingPlayerIds=sorted(set(expected)-actual),
                          extraPlayerIds=sorted(actual-set(expected)))
            if unresolved.get(code):
                raise ValueError('unresolved ESPN identities')
            if not 5 <= len(expected) <= 30 or not len(rows):
                raise ValueError('empty or invalid roster population')
            if actual & duplicate_ids or set(rows.ESPN_ID) & duplicate_espn:
                raise ValueError('duplicate ESPN roster identity')
            if any(not re.fullmatch(r'[1-9][0-9]*', str(value)) for value in rows.ESPN_ID):
                raise ValueError('invalid ESPN identity; reconcile original provider rows only')
            if set(rows.SEASON) != {season} or {_id(t) for t in rows.TEAM_ID} != {tid}:
                raise ValueError('ESPN season or team mismatch')
            if not actual <= set(expected):
                raise ValueError('ESPN extras require independent resolution')
            if actual == set(expected):
                detail['status'] = 'already_equal'
                continue
            url = f"https://www.nba.com/team/{tid}/{meta['name'].split()[-1].lower()}?view=roster"
            requested_at = _clock()
            response = transport(url, headers={'User-Agent': 'Mozilla/5.0', 'Cache-Control': 'no-cache'}, timeout=25)
            received_at = _clock()
            body = response.content
            detail['source'] = _save(directory/(code+'.html'), body)
            receipt = dict(url=url, finalUrl=response.url, requestedAt=requested_at,
                           receivedAt=received_at, observedAt=received_at,
                           publishedAt=None, statusCode=response.status_code,
                           headers=dict(response.headers), sha256=_sha(body), bytes=len(body))
            detail['receipt'] = _save(directory/(code+'.receipt.json'),
                                      (json.dumps(receipt, indent=2)+'\n').encode())
            detail['observedAt'] = received_at
            final = urlparse(response.url)
            if response.status_code != 200 or final.scheme != 'https' or final.netloc != 'www.nba.com' or final.path != urlparse(url).path:
                raise ValueError('official HTTP source mismatch')
            official = _official(body, tid, season)
            if set(official) != set(expected):
                raise ValueError('official canonical player set mismatch')
            if any(official[pid] != expected[pid]['n'] for pid in expected):
                raise ValueError('official canonical name mismatch')
            additions = []
            for pid in detail['missingPlayerIds']:
                player = expected[pid]
                additions.append(dict(PLAYER_ID=pid, ESPN_ID=f'nba:{pid}', PLAYER=player['n'],
                                      TEAM_ID=tid, TEAM=code, SEASON=season,
                                      IS_ROOKIE=bool(player.get('rookie', False)),
                                      DRAFT_SLOT=player.get('rateProvenance', {}).get('draftSlot')))
            out = pd.concat([out, pd.DataFrame(additions)], ignore_index=True)
            detail['appendedPlayerIds'] = detail['missingPlayerIds'][:]
            audit['appendedPlayerIds'].extend(detail['appendedPlayerIds'])
            detail['status'] = 'official_confirmed_subset'
        except (ValueError, KeyError, TypeError, OSError, requests.RequestException) as exc:
            detail['reason'] = str(exc)
            audit['heldTeams'].append(code)
    audit.update(outputRows=len(out), completedAt=_clock())
    return out, audit
