"""Export compact, auditable descriptive history for the daily research screen."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

STATS={'PTS':'points','REB':'reboundsTotal','AST':'assists','FG3M':'threePointersMade','FGA':'fieldGoalsAttempted','FG3A':'threePointersAttempted'}

def export(source, destination):
    columns=['PLAYER_ID','SEASON','SEASON_TYPE','GAME_DATE','minutes_target_valid','minutes_value',*STATS.values()]
    d=pd.read_parquet(source,columns=columns)
    d=d[d.SEASON_TYPE.eq('Regular Season') & d.SEASON.isin(['2024-25','2025-26']) & d.minutes_target_valid & d.minutes_value.gt(0)]
    players={}
    for (pid,season),q in d.groupby(['PLAYER_ID','SEASON']):
        stats={}
        for name,column in STATS.items():
            valid=q[column].dropna()
            if (valid.lt(0)|valid.mod(1).ne(0)).any():raise ValueError('Invalid count statistic')
            stats[name]={'n':len(valid),'counts':{str(int(k)):int(v) for k,v in valid.value_counts().sort_index().items()}}
        players.setdefault(str(int(pid)),{})[season]={'appearances':len(q),'meanMinutes':float(q.minutes_value.mean()),'stats':stats}
    result={'schemaVersion':1,'seasons':['2024-25','2025-26'],'latestGameDate':str(d.GAME_DATE.max().date()),
        'sourceSha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'method':'Descriptive regular-season appearances with valid positive minutes; includes previous teams. DNPs excluded. Not forecast probabilities.',
        'players':players}
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(result,separators=(',',':'),allow_nan=False)+'\n')
    print(json.dumps({'players':len(players),'bytes':destination.stat().st_size}))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    a=parser.parse_args();export(a.input,a.out)
