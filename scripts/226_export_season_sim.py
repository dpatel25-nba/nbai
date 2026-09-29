"""Export the official 2026–27 schedule, full rosters and season simulation inputs.

The PDF is a cached primary source, not a synthetic schedule. Provisional Cup
games are generated separately in the browser and never enter this export.
"""
import argparse
import hashlib
import importlib.util
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import fitz
import numpy as np
import pandas as pd
from nba_api.stats.static.teams import get_teams
from season_impact import impact_profiles

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT/'data/raw/season_sim_2026_27'
OUT = ROOT/'web/season-data.js'
SEASON = '2026-27'
SCHEDULE_URL = 'https://ak-static.cms.nba.com/wp-content/uploads/sites/46/2026/08/2026-27-NBA-Regular-Season-Schedule-By-Date.pdf'
EAST = set('ATL BOS BKN CHA CHI CLE DET IND MIA MIL NYK ORL PHI TOR WAS'.split())
NAMES = {'Atlanta':'ATL','Boston':'BOS','Brooklyn':'BKN','Charlotte':'CHA','Chicago':'CHI',
    'Cleveland':'CLE','Dallas':'DAL','Denver':'DEN','Detroit':'DET','Golden State':'GSW',
    'Houston':'HOU','Indiana':'IND','LA Clippers':'LAC','LA Lakers':'LAL','Memphis':'MEM',
    'Miami':'MIA','Milwaukee':'MIL','Minnesota':'MIN','New Orleans':'NOP','New York':'NYK',
    'Oklahoma City':'OKC','Orlando':'ORL','Philadelphia':'PHI','Phoenix':'PHX','Portland':'POR',
    'Sacramento':'SAC','San Antonio':'SAS','Toronto':'TOR','Utah':'UTA','Washington':'WAS'}


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def parse_schedule(path):
    pattern = re.compile(r'^\s*(\d+)\s+\w+\.\s+(\d+/\d+/\d+)\s+(.+?)\s+(?:at|vs)\s+(.+?)\s+'
                         r'(\d+:\d+\s+[AP]M)\s+(\d+:\d+\s+[AP]M)(.*)$')
    games, rejected = [], []
    with fitz.open(path) as doc:
        for page in doc:
            for line in page.get_text(sort=True).splitlines():
                match = pattern.match(line)
                if not match:
                    if re.match(r'^\s*\d+\s+\w+\.\s+\d+/\d+/\d+', line):
                        rejected.append(line)
                    continue
                number, date, away, home, local, eastern, flags = match.groups()
                away, home = ' '.join(away.split()), ' '.join(home.split())
                day = datetime.strptime(date,'%m/%d/%y').strftime('%Y-%m-%d')
                tip = pd.Timestamp(datetime.strptime(day+' '+' '.join(eastern.split()),'%Y-%m-%d %I:%M %p')).tz_localize('America/New_York')
                games.append(dict(id='00226'+number.zfill(5),date=day,away=NAMES[away],home=NAMES[home],
                    tip=tip.isoformat(),timeET=' '.join(eastern.split()),cup='C' in flags.split(),
                    neutral=bool(set(flags.split()) & {'B','D','E'}),provisional=False))
    if rejected:
        raise ValueError('Unparsed schedule lines: '+repr(rejected[:5]))
    validate_schedule(games)
    return sorted(games,key=lambda g:(g['tip'],g['id']))


def validate_schedule(games):
    counts = Counter(t for g in games for t in [g['home'],g['away']])
    if len(games) != 1200 or len({g['id'] for g in games}) != 1200:
        raise ValueError('Expected 1,200 unique assigned regular-season games')
    if set(counts) != set(NAMES.values()) or set(counts.values()) != {80}:
        raise ValueError('Every team must have 80 official assigned games')
    occupied = Counter((g['date'],t) for g in games for t in [g['home'],g['away']])
    if max(occupied.values()) != 1 or any(g['home']==g['away'] for g in games):
        raise ValueError('Invalid same-day schedule collision')
    if min(g['date'] for g in games) != '2026-10-20' or max(g['date'] for g in games) != '2027-04-11':
        raise ValueError('Wrong schedule season')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--download-schedule',action='store_true',help='Refresh the official release PDF before exporting.')
    args = ap.parse_args()
    pdf = RAW/'schedule_by_date.pdf'
    if args.download_schedule:
        RAW.mkdir(parents=True,exist_ok=True)
        with urlopen(Request(SCHEDULE_URL,headers={'User-Agent':'Mozilla/5.0'}),timeout=40) as response:
            body = response.read();final_url = response.url
        if not body.startswith(b'%PDF') or final_url != SCHEDULE_URL:
            raise ValueError('Schedule response is not the expected official PDF')
        pending = RAW/'schedule_by_date.pending.pdf';pending.write_bytes(body)
        parse_schedule(pending)  # Never replace a good cache with another season or a partial schedule.
        pending.replace(pdf)
        pdf.with_name(pdf.name+'.receipt.json').write_text(json.dumps(dict(url=SCHEDULE_URL,
            final_url=final_url,retrieved_at=datetime.now(timezone.utc).isoformat(),sha256=digest(pdf)),indent=2))
    receipt = json.loads(pdf.with_name(pdf.name+'.receipt.json').read_text())
    if digest(pdf) != receipt['sha256'] or receipt['url'] != SCHEDULE_URL or receipt['final_url'] != SCHEDULE_URL:
        raise ValueError('Schedule source checksum changed')
    schedule = parse_schedule(pdf)
    sim_path = ROOT/'data/features/web_sim.json'
    sim = json.loads(sim_path.read_text())
    roster_path = ROOT/'data/parquet/team_rosters.parquet'
    roster = pd.read_parquet(roster_path).query('SEASON == @SEASON')
    if roster.TEAM.nunique() != 30 or roster.PLAYER_ID.duplicated().any():
        raise ValueError('Need full unique-player 2026–27 rosters')
    history_path = ROOT/'data/parquet/player_seasons.parquet'
    history = pd.read_parquet(history_path)
    history = history[history.SEASON.lt(SEASON)].copy()
    history['FG2A_36'] = history.FGA_36-history.FG3A_36
    history['FG2_PCT'] = ((history.FGM_36-history.FG3M_36)/history.FG2A_36.clip(lower=.1)).clip(0,1)
    spec = importlib.util.spec_from_file_location('season_rates',ROOT/'scripts/124_possession_sim.py')
    engine = importlib.util.module_from_spec(spec); spec.loader.exec_module(engine)
    cols = engine.RATE_COLS+engine.PCT_COLS+['FG2A_36','FG2_PCT']
    shrink = engine.load_K()
    latest = sorted(history.SEASON.unique())[-1]
    prior = {c:float(np.average(history.loc[history[c].notna(),c],weights=history.loc[history[c].notna(),'MIN'])) for c in cols}
    # Same recency and shrinkage family as the existing engine, evaluated for
    # an upcoming season without requiring future outcome rows in the facts.
    rates = {}
    by_player = {int(pid):group.set_index('SEASON') for pid,group in history.groupby('PLAYER_ID')}
    for pid in roster.PLAYER_ID:
        group = by_player.get(int(pid))
        if group is None:
            continue
        profile = {}
        for c in cols:
            k = float(shrink.get(c,engine.K)); num = den = 0.
            for lag,w in engine.RECENCY.items():
                year = int(SEASON[:4])-lag; season = f'{year}-{str(year+1)[-2:]}'
                if season not in group.index:
                    continue
                row = group.loc[season];v,m = row[c],row.MIN
                if pd.notna(v) and pd.notna(m):
                    num += w*m*v;den += w*m
            profile[c] = (num+k*prior[c])/(den+k)
        rates[int(pid)] = profile
    engine._apply_aging(rates,SEASON)
    fallback = {c:float(np.median([r[c] for r in rates.values()])) for c in cols}
    for c in ['FG2A_36','FG3A_36','FTA_36','AST_36']:
        fallback[c] *= .8
    fallback['MPG'] = 12.
    book = engine.RateBook(rates,fallback,*engine.rookie_profiles(cols,fallback))
    for r in roster.itertuples():
        if pd.notna(r.DRAFT_SLOT):
            book.slot_of[int(r.PLAYER_ID)] = int(r.DRAFT_SLOT)
    impact_path = ROOT/'data/parquet/player_seasons_war_v3.parquet'
    impact = impact_profiles(pd.read_parquet(impact_path).to_dict('records'),
        roster.PLAYER_ID, SEASON, lookback=max(engine.RECENCY))
    players = {}
    for row in roster.itertuples():
        pid = int(row.PLAYER_ID);profile = book[pid]
        p = {c:round(float(profile[c]),5) for c in cols}
        if not all(np.isfinite(v) and v >= 0 for v in p.values()):
            raise ValueError('Invalid rate profile: '+str(pid))
        p.update(id=pid,n=row.PLAYER,team=row.TEAM,pos=row.POSITION,rookie=bool(row.IS_ROOKIE),
            rateSource='projected_history' if pid in rates else ('rookie_prior' if row.IS_ROOKIE else 'replacement_prior'))
        p.update(impact[pid])
        players[str(pid)] = p
    details = {t['abbreviation']:t for t in get_teams()}
    teams = {}
    for abbr in sorted(sim['teams']):
        t = sim['teams'][abbr];e = sim['engine']['teams'][abbr]
        teams[abbr] = dict(name=details[abbr]['full_name'],conference='East' if abbr in EAST else 'West',
            id=t['id'],off=t['off'],defense=t['def'],pace=e['pace'],baseBpm=e['base_bpm'])
    source_paths = [pdf,roster_path,history_path,impact_path,sim_path,Path(__file__),
        ROOT/'scripts/124_possession_sim.py',ROOT/'scripts/season_impact.py',
        ROOT/'web/season-engine.js',ROOT/'web/season-possession.js']
    source_paths += [p for p in [engine.SHRINK,engine.ROOKIE,engine.RK_CMB,engine.AGING,engine.BIO] if p.exists()]
    payload = dict(season=SEASON,generated=datetime.now(timezone.utc).isoformat(),
        scheduleAsOf='2026-08-13',scheduleSource=receipt['url'],scheduleSha256=digest(pdf),
        rosterAsOf=datetime.fromtimestamp(roster_path.stat().st_mtime,timezone.utc).strftime('%Y-%m-%d'),
        ratingsSeason=sim['season'],ratesThrough=latest,schedule=schedule,teams=teams,players=players,
        mu0=sim['mu0'],hca=sim['hca'],constants=sim['engine']['const'],league=sim['engine']['lg'],
        calibration=sim['engine']['cal'],
        impactPolicy=dict(version=2,lookbackSeasons=max(engine.RECENCY),prior=-1.5,
            sources=dict(Counter(p['impactSource'] for p in players.values())),
            note='Impact uses the latest finite pre-season rating within three seasons. Missing recent seasons carry historical impact forward; older or absent history uses a labeled prior. Historical carry-forward is not an injury-recovery forecast or an age-adjusted impact projection.'),
        sourceHashes={str(p.relative_to(ROOT)):digest(p) for p in source_paths},
        modelNote='Scenario simulation using projected player rates, prior-season team ratings and minutes-weighted BPM roster adjustments. Full-season forecasts and transfer effects have not been independently backtested. All players are available; no future injuries or trades are generated.')
    OUT.write_text('window.NBAI_SEASON_DATA = '+json.dumps(payload,separators=(',',':'),allow_nan=False)+';\n')
    print(f'Exported {len(schedule)} official games, {len(teams)} teams and {len(players)} rostered players to {OUT}')


if __name__ == '__main__':
    main()
