"""Export descriptive historical research. No simulated seasons or betting signals."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
ZONES=['rim','paint','mid','corner3','arc3']

def aggregate_shots(frame):
    """Counts reconcile before/after coordinate validation; grid is 2.5-foot squares."""
    f=frame[(frame.SEASON_TYPE=='Regular Season') & (frame.IS_FIELD_GOAL==1) & frame.SHOT_RESULT.isin(['Made','Missed']) & frame.SHOT_VALUE.isin([2,3])].copy()
    if f.duplicated(['GAME_ID','ACTION_NUMBER']).any():raise ValueError('Duplicate shot events')
    valid=np.isfinite(f.SHOT_X)&np.isfinite(f.SHOT_Y)&np.isfinite(f.SHOT_DISTANCE)&f.SHOT_X.between(-250,250)&f.SHOT_Y.between(-52.5,887.5)&(f.SHOT_DISTANCE>=0)&f.TEAM_TRICODE.notna()
    v=f[valid].copy();v['made']=(v.SHOT_RESULT=='Made').astype(int)
    v['zone']=np.where(v.SHOT_VALUE==3,np.where((v.SHOT_X.abs()>=220)&(v.SHOT_Y<=92.5),'corner3','arc3'),np.where(v.SHOT_DISTANCE<4,'rim',np.where(v.SHOT_DISTANCE<14,'paint','mid')))
    v['onCourt']=v.SHOT_Y<=417.5
    v['gx']=np.floor((v.SHOT_X+250)/25).clip(0,19).astype(int);v['gy']=np.floor((v.SHOT_Y+52.5)/25).clip(0,18).astype(int)
    def record(d,raw):
        grid=d[d.onCourt].groupby(['gx','gy']).made.agg(['size','sum'])
        zones=d.groupby('zone').made.agg(['size','sum'])
        return dict(attempts=len(d),made=int(d.made.sum()),rawAttempts=len(raw),excludedCoordinates=len(raw)-len(d),beyondHalfCourt=int((~d.onCourt).sum()),games=int(raw.GAME_ID.nunique()),cells=[[int(x),int(y),int(r['size']),int(r['sum'])] for (x,y),r in grid.iterrows()],zones={z:{'attempts':int(zones.loc[z,'size']) if z in zones.index else 0,'made':int(zones.loc[z,'sum']) if z in zones.index else 0} for z in ZONES})
    return {'league':record(v,f),'teams':{t:record(v[v.TEAM_TRICODE==t],f[f.TEAM_TRICODE==t]) for t in sorted(f.TEAM_TRICODE.dropna().unique())}}

def build(root=ROOT,seasons=('2023-24','2024-25','2025-26')):
    parquet=root/'data/parquet';ps=pd.read_parquet(parquet/'player_seasons.parquet');war=pd.read_parquet(parquet/'player_seasons_war_v4.parquet');games=pd.read_parquet(parquet/'games.parquet')
    inputs=['data/parquet/player_seasons.parquet','data/parquet/player_seasons_war_v4.parquet','data/parquet/games.parquet','scripts/129_war_v4.py','scripts/21_build_pbp.py','scripts/22_build_player_seasons.py','scripts/build_visual_research.py']
    result={'schemaVersion':1,'generatedAt':datetime.now(timezone.utc).isoformat(),'scope':'Historical regular seasons; descriptive, not a forecast','minMinutes':500,'gridFeet':2.5,'cellMinAttempts':20,'leagueCellMinAttempts':100,'seasons':[]}
    for season in seasons:
        path=f'data/parquet/pbp/{season}.parquet';inputs.append(path);shots=aggregate_shots(pd.read_parquet(root/path))
        base=ps[ps.SEASON==season].copy()
        if base.PLAYER_ID.duplicated().any():raise ValueError('Duplicate player seasons')
        joined=base.merge(war[war.SEASON==season][['PLAYER_ID','OBPM4','DBPM4','WAR4']],on='PLAYER_ID',validate='one_to_one',how='left')
        lg_ts=float(base.points.sum()/(2*(base.fieldGoalsAttempted.sum()+.44*base.freeThrowsAttempted.sum()))*100)
        players=[]
        for r in joined[joined.MIN>=500].itertuples():
            vals=[r.OBPM4,r.DBPM4,r.WAR4,r.USG,r.points,r.fieldGoalsAttempted,r.freeThrowsAttempted]
            if not all(np.isfinite(x) for x in vals):continue
            tsa=r.fieldGoalsAttempted+.44*r.freeThrowsAttempted
            if tsa<=0:continue
            players.append(dict(id=int(r.PLAYER_ID),name=r.PLAYER,team=r.TEAM,teams=int(r.N_TEAMS),minutes=round(r.MIN,1),games=int(r.GP),off=round(r.OBPM4,3),defense=round(r.DBPM4,3),war=round(r.WAR4,3),usage=round(r.USG*100,3),ts=round(r.points/(2*tsa)*100,3)))
        expected=games[(games.SEASON==season)&(games.SEASON_TYPE=='Regular Season')]
        result['seasons'].append(dict(season=season,players=players,leagueTS=round(lg_ts,3),expectedGames=len(expected),start=str(expected.GAME_DATE.min().date()),end=str(expected.GAME_DATE.max().date()),**shots))
    result['sources']=[dict(path=p,sha256=hashlib.sha256((root/p).read_bytes()).hexdigest()) for p in inputs]
    result['method']={'impact':'WAR v4 offensive and defensive components: standardized box, play-type, creation, rim-protection and hustle blends. Historical associations, not causal or independently validated impact. Missing source components were filled with zero by the original formula; component scales use the historical fitted population.','efficiency':'True shooting = points / [2 × (FGA + 0.44 × FTA)]. Relative TS subtracts the full-league, attempt-weighted season rate. Usage is the archived season usage rate.','shots':'NBA play-by-play xLegacy/yLegacy, in tenths of a foot relative to the basket. Rendered in 2.5-foot bins. Rim: 2PT under 4 ft; paint: 2PT 4–under 14 ft; midrange: other 2PT; corner 3: |x| ≥ 22 ft and y ≤ 9.25 ft; other threes: arc 3, including heaves. Displayed half court excludes shots beyond 47 ft from baseline; zone counts retain them.','teams':'Historical team labels. Multi-team players are marked; their metrics cover their full season, not only the displayed team.','provenance':'Archived NBA play-by-play and box-score data, transformed by the listed local scripts. Input hashes identify snapshots; they do not establish independent accuracy.'}
    out=root/'web/visual-research.json';out.write_text(json.dumps(result,separators=(',',':'),allow_nan=False)+'\n');print(json.dumps({'output':str(out),'seasons':[{ 'season':s['season'],'players':len(s['players']),'teams':len(s['teams']),'games':s['league']['games'],'shots':s['league']['attempts'],'excluded':s['league']['excludedCoordinates']} for s in result['seasons']]}))
    return result
if __name__=='__main__':build()
