"""Prospective inputs, predictions and immutable archives for local shadow runs."""
import hashlib
import importlib.util
import json
import re
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from pregame_research import build_features, TARGETS

ROOT=Path(__file__).resolve().parents[1]
BASE="https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ALIASES={"NY":"NYK","SA":"SAS","GS":"GSW","PHO":"PHX","NO":"NOP","UTAH":"UTA","WSH":"WAS"}
OUT_STATUSES={"out","injured reserve","suspension","not with team"}
TEAM_NAMES=dict(zip(
    'ATL BOS BKN CHA CHI CLE DAL DEN DET GSW HOU IND LAC LAL MEM MIA MIL MIN NOP NYK OKC ORL PHI PHX POR SAC SAS TOR UTA WAS'.split(),
    ['Atlanta Hawks','Boston Celtics','Brooklyn Nets','Charlotte Hornets','Chicago Bulls',
     'Cleveland Cavaliers','Dallas Mavericks','Denver Nuggets','Detroit Pistons','Golden State Warriors',
     'Houston Rockets','Indiana Pacers','LA Clippers','Los Angeles Lakers','Memphis Grizzlies',
     'Miami Heat','Milwaukee Bucks','Minnesota Timberwolves','New Orleans Pelicans','New York Knicks',
     'Oklahoma City Thunder','Orlando Magic','Philadelphia 76ers','Phoenix Suns','Portland Trail Blazers',
     'Sacramento Kings','San Antonio Spurs','Toronto Raptors','Utah Jazz','Washington Wizards']))


class NotPregame(ValueError):pass


def utcnow():return datetime.now(timezone.utc)


def normalize(name):
    s="".join(c for c in unicodedata.normalize("NFKD",str(name)) if not unicodedata.combining(c))
    s=s.lower().replace("'","").replace("."," ").replace("-"," ").replace(","," ")
    # Suffixes distinguish real people (Jameer Nelson vs Jameer Nelson Jr.).
    return " ".join(s.split())


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    # Snapshots never overwrite a previous observation.
    with path.open("x") as f:json.dump(data,f,indent=2,allow_nan=False)


def fetch_json(url,path):
    with urllib.request.urlopen(url,timeout=25) as r:data=json.load(r)
    fetched=utcnow().isoformat()
    write_json(path,dict(url=url,fetched_at=fetched,payload=data))
    return data,fetched


def team_map():
    g=pd.read_parquet(ROOT/"data/parquet/games.parquet")
    return {str(r.HOME_TEAM):int(r.HOME_TEAM_ID) for r in g.itertuples()}


def parse_schedule(payload,teams):
    if not isinstance(payload.get("events"),list):raise ValueError("scoreboard has no events list")
    out=[]
    for e in payload["events"]:
        if e.get("season",{}).get("type")!=2:continue  # RS model; no preseason masquerading as RS
        c=e["competitions"][0]
        if not c.get("timeValid",False):continue
        sides={x["homeAway"]:x for x in c["competitors"]}
        ab={s:ALIASES.get(x["team"]["abbreviation"],x["team"]["abbreviation"]) for s,x in sides.items()}
        if set(ab)!={"home","away"} or any(a not in teams for a in ab.values()):raise ValueError("unknown scheduled NBA team")
        tip=pd.Timestamp(c.get("date",e["date"]))
        if tip.tzinfo is None:raise ValueError("scheduled tipoff lacks timezone")
        year=int(e["season"]["year"])
        status=c.get("status",e.get("status",{})).get("type",{})
        out.append(dict(GAME_ID="espn:"+str(e["id"]),EVENT_ID=str(e["id"]),
            GAME_DATE=tip.tz_convert("America/New_York").normalize().tz_localize(None),
            TIPOFF_UTC=tip.tz_convert("UTC").isoformat(),SEASON=f"{year-1}-{str(year)[-2:]}",
            SEASON_TYPE="Regular Season",HOME_TEAM_ID=teams[ab["home"]],AWAY_TEAM_ID=teams[ab["away"]],
            HOME_TEAM=ab["home"],AWAY_TEAM=ab["away"],STATE=status.get("state"),
            COMPLETED=bool(status.get("completed",False))))
    return out


def identities():
    old=pd.read_parquet(ROOT/"data/parquet/team_rosters.parquet")
    pg=pd.read_parquet(ROOT/"data/parquet/player_games.parquet",columns=["PLAYER_ID","firstName","familyName"])
    names={}
    for pid,n in list(zip(old.PLAYER_ID,old.PLAYER))+[(r.PLAYER_ID,f"{r.firstName} {r.familyName}") for r in pg.drop_duplicates().itertuples()]:
        names.setdefault(normalize(n),set()).add(int(pid))
    meta={int(r.PLAYER_ID):r._asdict() for r in old.sort_values("SEASON").itertuples()}
    aliases=ROOT/"scripts/player_identity_overrides.json"
    if aliases.exists():
        for a in json.loads(aliases.read_text()):
            names.setdefault(normalize(a["name"]),set()).add(int(a["nba_id"]))
            meta.setdefault(int(a["nba_id"]),dict(IS_ROOKIE=True,DRAFT_SLOT=0))
    return names,meta


def parse_roster(payload,tid,abbr,names,meta):
    athletes=payload.get("athletes")
    if not isinstance(athletes,list) or len(athletes)<8:raise ValueError(f"incomplete roster: {abbr}")
    actual_abbr=payload.get("team",{}).get("abbreviation",abbr)
    if ALIASES.get(actual_abbr,actual_abbr)!=abbr:raise ValueError("roster endpoint returned another team")
    season=payload.get("season",{}).get("displayName")
    if not season or not re.fullmatch(r"20\d\d-\d\d",season):raise ValueError("roster season missing")
    rows=[];unresolved=[]
    for a in athletes:
        nm=a.get("displayName",a.get("fullName",""));ids=names.get(normalize(nm),set())
        if len(ids)!=1:
            unresolved.append(dict(name=nm,espn_id=str(a["id"]),reason="unknown or ambiguous NBA id"));continue
        pid=next(iter(ids));old=meta.get(pid,{})
        rows.append(dict(PLAYER_ID=pid,ESPN_ID=str(a["id"]),PLAYER=nm,TEAM_ID=tid,TEAM=abbr,SEASON=season,
                         IS_ROOKIE=bool(old.get("IS_ROOKIE",False)),DRAFT_SLOT=old.get("DRAFT_SLOT",np.nan)))
    if len({r["PLAYER_ID"] for r in rows})!=len(rows):raise ValueError("duplicate roster identity")
    return rows,unresolved


def parse_injuries(payload,rosters):
    if not isinstance(payload.get("injuries"),list):raise ValueError("invalid injury response")
    team_names={normalize(name):code for code,name in TEAM_NAMES.items()}
    team_names.update({normalize('Los Angeles Clippers'):'LAC',normalize('LA Lakers'):'LAL'})
    by_id={str(r.ESPN_ID):r for r in rosters.itertuples()}
    by_name={}
    for r in rosters.itertuples():by_name.setdefault((r.TEAM,normalize(r.PLAYER)),[]).append(r)
    statuses={};unresolved=[]
    for team in payload["injuries"]:
        code=team_names.get(normalize(team.get('displayName','')))
        for e in team.get("injuries",[]):
            a=e.get("athlete",{});row=by_id.get(str(a.get("id")))
            matches=by_name.get((code,normalize(a.get("displayName",""))),[])
            if row is None and len(matches)==1:row=matches[0]
            if row is None or code is None or row.TEAM!=code:
                unresolved.append(dict(name=a.get("displayName"),team=team.get("displayName"),
                    team_code=code,reason='injury identity or current team unresolved'));continue
            if normalize(row.PLAYER)!=normalize(a.get("displayName",row.PLAYER)):
                unresolved.append(dict(name=a.get('displayName'),team=team.get('displayName'),
                    team_code=code,reason='injury athlete id/name disagreement'));continue
            st=str(e.get("status","")).strip()
            statuses[row.PLAYER_ID]=dict(status=st,out=st.lower() in OUT_STATUSES)
    return statuses,unresolved


def parse_starters(summary,event):
    # Only explicit starter flags for this exact pregame event are accepted.
    header=summary.get("header",{})
    if str(header.get("id"))!=event["EVENT_ID"]:raise ValueError("summary event id mismatch")
    c=header.get("competitions",[{}])[0]
    state=c.get("status",{}).get("type",{}).get("state")
    if state in ("in","post"):raise NotPregame("summary says the game has started")
    if c.get("date") and abs((pd.Timestamp(c["date"])-pd.Timestamp(event["TIPOFF_UTC"])).total_seconds())>60:
        raise NotPregame("summary and schedule disagree on tipoff; await a refreshed schedule")
    if state!="pre":return set()
    starters=set()
    for t in summary.get("boxscore",{}).get("players",[]):
        flagged=set()
        for group in t.get("statistics",[]):
            for a in group.get("athletes",[]):
                if a.get("starter") is True:flagged.add(str(a["athlete"]["id"]))
        if len(flagged)==5:starters.update(flagged)
    return starters


def load_bundle():
    p=ROOT/"data/features/shadow/models/current.json"
    current=json.loads(p.read_text());path=Path(current["path"])
    if sha(path)!=current["artifact_sha256"]:raise ValueError("model artifact checksum mismatch")
    return joblib.load(path),current


def live_features(events,rosters,statuses,pg,games):
    future=pd.DataFrame(events)
    # All live targets remain null. History is restricted before the earliest
    # slate date; conservative for forecasts made after another game that day.
    day=future.GAME_DATE.min()
    games=games[(games.GAME_DATE<day)&(games.SEASON_TYPE=="Regular Season")]
    pg=pg[pg.GAME_ID.isin(games.GAME_ID)]
    rs={}
    for e in events:
        for tid in (e["HOME_TEAM_ID"],e["AWAY_TEAM_ID"]):
            rs[(e["GAME_ID"],tid)]=rosters.loc[(rosters.TEAM_ID==tid)&(rosters.SEASON==e["SEASON"]),"PLAYER_ID"].astype(int).tolist()
            if len(rs[(e["GAME_ID"],tid)])<8:raise ValueError("insufficient current roster for live event")
    d=build_features(pg,pd.concat([games,future],ignore_index=True),future_rosters=rs,emit_game_ids=set(future.GAME_ID))
    d=d.merge(rosters[["PLAYER_ID","ESPN_ID","PLAYER","TEAM_ID"]],on=["PLAYER_ID","TEAM_ID"],validate="many_to_one")
    # Explicitly unavailable players remain forecast rows at zero; role context
    # for their teammates uses the known available roster. This overlay is new
    # prospective behavior, not part of the historical confirmation claim.
    d["out"]=d.PLAYER_ID.map(lambda p:statuses.get(p,{}).get("out",False))
    d["injury_status"]=d.PLAYER_ID.map(lambda p:statuses.get(p,{}).get("status","Not listed"))
    for _,ix in d.groupby(["GAME_ID","TEAM_ID"]).groups.items():
        q=d.loc[ix];available=q[~q.out];total=available.ewm_min.sum()
        pts=(available.ewm_min*available.prior_pts36).sum()
        own=np.where(q.out,0.,q.ewm_min)
        d.loc[ix,"team_expected_min"]=total
        d.loc[ix,"teammate_expected_min"]=total-own
        d.loc[ix,"teammate_pts36"]=(pts-own*q.prior_pts36)/np.maximum(total-own,1.)
        d.loc[ix,"own_min_share"]=own/max(total,1.)
        d.loc[ix,"previous_absent_min"]=q.loc[q.out|(q.last_min==0),"prior_mpg"].sum()
    return d


def shadow_predictions(d,bundle,starters):
    d=d.copy();models=bundle["models"];fs=bundle["manifest"]["features"]
    predict=lambda name:np.maximum(models[name].predict(d[fs[name]]),0.)
    d["baseline_minutes"]=np.clip(predict("baseline_minutes"),0,48)
    d["p_play_history"]=models["play"].predict_proba(d[fs["play"]])[:,1]
    d["reported_starter"]=d.ESPN_ID.astype(str).isin(starters)
    # Record announced starters, but don't force p=1 from a flag that can still
    # change before tipoff. Out is the only deterministic availability override.
    d["p_play"]=np.where(d.out,0,d.p_play_history)
    d["candidate_minutes"]=d.p_play*np.clip(predict("conditional_minutes"),0,48)
    d.loc[d.out,"baseline_minutes"]=0.
    for version in ("baseline","candidate"):
        d["pred_min"]=d[f"{version}_minutes"]
        for stat in ("points","reb","ast"):
            d[f"{version}_{stat}"]=predict(f"{version}_{stat}")
            d.loc[d.out,f"{version}_{stat}"]=0.
    return d


def apply_live_guards(rows):
    """Conservative fallback for an out-of-domain roster or stale tracking.

    Never erase the raw forecasts: score them separately to expose the failure
    rather than count an incumbent fallback as evidence for the new model.
    Bounds are operational guardrails, not tuned performance parameters.
    """
    rows=rows.copy()
    for version in ("baseline","candidate"):
        for stat in TARGETS:rows[f"raw_{version}_{stat}"]=rows[f"{version}_{stat}"]
        rows[f"{version}_fallback"]=""
        for _,ix in rows.groupby(["GAME_ID","TEAM_ID"]).groups.items():
            total=rows.loc[ix,f"{version}_minutes"].sum()
            if not 180<=total<=300:
                for stat in TARGETS:rows.loc[ix,f"{version}_{stat}"]=rows.loc[ix,f"incumbent_{stat}"]
                rows.loc[ix,f"{version}_fallback"]="team minutes outside 180–300; incumbent used"
    stale=((rows.tracking_games<3)|(rows.tracking_age>30))&~rows.out
    rows["assist_tracking_fallback"]=stale
    rows.loc[stale,"candidate_ast"]=rows.loc[stale,"incumbent_ast"]
    return rows


def archive_forecast(path,event,rows,bundle_id,input_paths,model_created,training_date,now=None):
    captured=now or utcnow();tip=pd.Timestamp(event["TIPOFF_UTC"])
    if pd.Timestamp(captured)>=tip:raise ValueError("refusing to archive a forecast after tipoff")
    if pd.Timestamp(model_created)>pd.Timestamp(captured):raise ValueError("model created after forecast")
    if pd.Timestamp(training_date).date()>=tip.date():raise ValueError("model training includes forecast date")
    if any(c in rows and rows[c].notna().any() for c in TARGETS):raise ValueError("live forecast contains outcomes")
    expected={f"{m}_{s}" for m in ("baseline","candidate","incumbent") for s in TARGETS}
    if not expected.issubset(rows.columns):raise ValueError("forecast is missing a model or target")
    if rows.empty or set(rows.GAME_ID)!={event["GAME_ID"]}:raise ValueError("forecast event mismatch")
    if set(rows.TEAM_ID)!={event["HOME_TEAM_ID"],event["AWAY_TEAM_ID"]}:raise ValueError("forecast missing a team")
    if rows.duplicated(["TEAM_ID","ESPN_ID"]).any():raise ValueError("duplicate forecast player")
    cols=list(expected)+[f"raw_{m}_{s}" for m in ("candidate","baseline") for s in TARGETS if f"raw_{m}_{s}" in rows]
    if not np.isfinite(rows[cols].to_numpy()).all() or (rows[cols]<0).any().any():raise ValueError("invalid forecast values")
    records=json.loads(rows.to_json(orient="records",date_format="iso"))
    data=dict(schema_version=1,event={k:str(v) if isinstance(v,pd.Timestamp) else v for k,v in event.items()},
        captured_at=pd.Timestamp(captured).isoformat(),model_id=bundle_id,model_created_at=model_created,
        training_last_date=training_date,lead_hours=(tip-pd.Timestamp(captured)).total_seconds()/3600,
        inputs={str(p):sha(p) for p in input_paths},predictions=records)
    write_json(path,data)
    return data


def parse_final(summary,event,teams):
    header=summary.get("header",{})
    if str(header.get("id"))!=event["EVENT_ID"]:raise ValueError("result event mismatch")
    c=header.get("competitions",[{}])[0]
    if not c.get("status",{}).get("type",{}).get("completed",False):return None
    official_tip=pd.Timestamp(c.get("date",header.get("date",event["TIPOFF_UTC"])))
    if official_tip.tzinfo is None:raise ValueError("result tipoff lacks timezone")
    expected={event["HOME_TEAM_ID"],event["AWAY_TEAM_ID"]};rows=[]
    for t in summary.get("boxscore",{}).get("players",[]):
        ab=t["team"]["abbreviation"];tid=teams[ALIASES.get(ab,ab)]
        group=next((x for x in t.get("statistics",[]) if {"MIN","PTS","REB","AST"}.issubset(x.get("labels",[]))),None)
        if group is None:raise ValueError("final box lacks required stats")
        for a in group.get("athletes",[]):
            vals=dict(zip(group["labels"],a.get("stats",[])));dnp=bool(a.get("didNotPlay",False))
            def count(label):
                if dnp:return 0.
                value=vals[label]
                if label=="MIN" and ":" in str(value):
                    m,s=str(value).split(":");return float(m)+float(s)/60
                return float(value)
            row=dict(ESPN_ID=str(a["athlete"]["id"]),PLAYER=a["athlete"].get("displayName",""),
                     TEAM_ID=tid,minutes=count("MIN"),points=count("PTS"),reb=count("REB"),ast=count("AST"),
                     started=bool(a.get("starter",False)))
            for label,names in (("FG",("fieldGoalsMade","fieldGoalsAttempted")),("3PT",("threePointersMade","threePointersAttempted")),("FT",("freeThrowsMade","freeThrowsAttempted"))):
                parts=str(vals.get(label,"0-0")).split("-")
                for name,val in zip(names,parts):row[name]=float(val) if not dnp else 0.
            rows.append(row)
    df=pd.DataFrame(rows)
    if df.empty or set(df.TEAM_ID)!=expected or df.duplicated(["ESPN_ID","TEAM_ID"]).any():raise ValueError("incomplete final box")
    for tid,g in df.groupby("TEAM_ID"):
        if (g.minutes>0).sum()<5 or g.minutes.sum()<230:raise ValueError("final minutes incomplete")
        competitor=next(x for x in c["competitors"] if teams[ALIASES.get(x["team"]["abbreviation"],x["team"]["abbreviation"])]==tid)
        if abs(g.points.sum()-float(competitor["score"]))>.1:raise ValueError("final points do not reconcile")
    return dict(event=event,actual_tipoff=official_tip.isoformat(),players=rows,collected_at=utcnow().isoformat())


def score_snapshot(snapshot,result):
    if pd.Timestamp(snapshot["captured_at"])>=pd.Timestamp(result["actual_tipoff"]):raise ValueError("forecast was not pregame at actual tipoff")
    a=pd.DataFrame(result["players"]);p=pd.DataFrame(snapshot["predictions"])
    if p.duplicated(["TEAM_ID","ESPN_ID"]).any():raise ValueError("duplicate forecast players")
    versions=["baseline","candidate","incumbent"]+[m for m in ("raw_baseline","raw_candidate") if all(f"{m}_{s}" in p for s in TARGETS)]
    predcols=[f"{m}_{s}" for m in versions for s in TARGETS]
    merged=p[["ESPN_ID","TEAM_ID"]+predcols].merge(a,on=["ESPN_ID","TEAM_ID"],how="outer",validate="one_to_one")
    merged[predcols+list(TARGETS)]=merged[predcols+list(TARGETS)].fillna(0.)
    out=[]
    for m in versions:
        for s in TARGETS:
            err=merged[f"{m}_{s}"]-merged[s]
            out.append(dict(model=m,target=s,n=len(err),absolute_error=float(err.abs().sum()),
                            signed_error=float(err.sum()),mae=float(err.abs().mean())))
    return out


class Incumbent:
    """Run the existing possession engine alongside the new count models."""
    def __init__(self,roster_path):
        def load(name,file):
            spec=importlib.util.spec_from_file_location(name,ROOT/"scripts"/file)
            m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
        self.S=load("shadow_sim124","124_possession_sim.py")
        self.M=load("shadow_matchup154","154_matchup.py")
        self.S.ROSTERS=Path(roster_path);self.M.ROSTERS=Path(roster_path)
        games=pd.read_parquet(ROOT/"data/parquet/games.parquet")
        self.season=sorted(games.SEASON.unique())[-1]
        self.rates,self.pos=self.S.build_rates(self.season)
        self.off,self.defense,self.mu,self.hca,_=self.M.team_ratings(self.season)
        rs=pd.read_parquet(roster_path);self.rseason=sorted(rs.SEASON.unique())[-1]
        for r in rs[rs.DRAFT_SLOT.notna()].itertuples():
            if int(r.PLAYER_ID) not in self.rates:self.rates.slot_of[int(r.PLAYER_ID)]=int(r.DRAFT_SLOT)
        tmpl=pd.read_parquet(ROOT/"data/parquet/rotation_templates.parquet")
        aff=pd.read_parquet(ROOT/"data/parquet/assignment_affinity.parquet").set_index("dpos")[self.S.POSITIONS].to_numpy()
        dq=self.S.defensive_index(self.season)
        if dq and isinstance(next(iter(dq)),tuple):dq={}
        pace,league_pace=self.S.team_pace(self.season)
        w=pd.read_parquet(ROOT/"data/parquet/player_seasons_war_v3.parquet")
        bpm={int(r.PLAYER_ID):r.BPM3 for r in w[w.SEASON==self.season].itertuples()}
        self.sim=self.S.Simulator(self.rates,self.pos,tmpl,aff,dq,pace,league_pace,bpm,
            self.S.minutes_ratio_pools(self.season),self.S.usage_ratio_pool(self.season))
        self.sources={str(p):sha(p) for p in (ROOT/"scripts").glob("*.py")}

    def predict(self,event,rows,statuses,sims=60):
        h,a=event["HOME_TEAM_ID"],event["AWAY_TEAM_ID"]
        outs={p for p,v in statuses.items() if v["out"]}
        sides=self.M.build_sides(self.season,h,a,outs,rates=self.rates,rseason=self.rseason)
        if any(len(x)<5 for x in sides.values()):raise ValueError("incumbent has fewer than five available players")
        anchor=(self.mu+self.off[h]-self.defense[a]+self.hca,self.mu+self.off[a]-self.defense[h])
        _,box,_=self.sim.simulate(sides["H"],sides["A"],h,a,n_sims=sims,seed=17,
            anchor=anchor,anchor_ref=self.M.prior_roster(self.season,h,a))
        rows=rows.copy()
        for stat in TARGETS:rows[f"incumbent_{stat}"]=0.
        for side,tid in (("H",h),("A",a)):
            for p in sides[side]:
                mask=(rows.TEAM_ID==tid)&(rows.PLAYER_ID==p["pid"])
                rows.loc[mask,"incumbent_minutes"]=p["minutes"]
                b=box[side].get(p["pid"],{})
                for stat,key in (("points","PTS"),("reb","REB"),("ast","AST")):
                    rows.loc[mask,f"incumbent_{stat}"]=float(np.mean(b[key])) if key in b else 0.
        return rows


def load_history(shadow_root):
    """Append final traditional facts gathered by this collector; prefer NBA facts
    if the main pipeline has since refreshed the same dated matchup. Tracking
    unavailable from the result source stays NaN, never a manufactured zero.
    """
    pg=pd.read_parquet(ROOT/"data/parquet/player_games.parquet")
    games=pd.read_parquet(ROOT/"data/parquet/games.parquet")
    matchup={(str(r.GAME_DATE.date()),int(r.HOME_TEAM_ID),int(r.AWAY_TEAM_ID)) for r in games.itertuples()}
    names,_=identities();extra_pg=[];extra_games=[]
    for path in sorted((Path(shadow_root)/"results").glob("*.json")):
        res=json.loads(path.read_text());e=res["event"]
        day=pd.Timestamp(res["actual_tipoff"]).tz_convert("America/New_York").normalize().tz_localize(None)
        key=(str(day.date()),e["HOME_TEAM_ID"],e["AWAY_TEAM_ID"])
        if key in matchup:continue
        extra_games.append(dict(GAME_ID=e["GAME_ID"],GAME_DATE=day,SEASON=e["SEASON"],SEASON_TYPE="Regular Season",
            HOME_TEAM_ID=e["HOME_TEAM_ID"],AWAY_TEAM_ID=e["AWAY_TEAM_ID"]))
        for a in res["players"]:
            ids=names.get(normalize(a["PLAYER"]),set())
            if len(ids)!=1:continue
            p=dict(a,PLAYER_ID=next(iter(ids)),GAME_ID=e["GAME_ID"],GAME_DATE=day,SEASON=e["SEASON"],
                SEASON_TYPE="Regular Season",MIN=a["minutes"],points=a["points"],reboundsTotal=a["reb"],
                assists=a["ast"],position="G" if a["started"] else "")
            tracking=Path(shadow_root)/"tracking"/f"{e['EVENT_ID']}.json"
            if tracking.exists():
                tracked=json.loads(tracking.read_text())["players"]
                p.update(tracked.get(str(p["PLAYER_ID"]),{}))
            # position here is only a nonempty starter flag; it is never used as
            # a positional/height input in the shadow feature generator.
            extra_pg.append(p)
    if extra_games:
        games=pd.concat([games,pd.DataFrame(extra_games)],ignore_index=True)
        pg=pd.concat([pg,pd.DataFrame(extra_pg)],ignore_index=True)
    return pg,games


def enrich_tracking(root,run):
    """Resolve ESPN finals to NBA game ids by dated team pair, then pull tracking.

    Retries unresolved finals on later runs. Failed NBA access leaves missing
    tracking explicit and does not replace it with a fabricated zero.
    """
    root=Path(root);pending=[p for p in (root/"results").glob("*.json") if not (root/"tracking"/p.name).exists()]
    if not pending:return dict(pending=0,downloaded=0,errors=[])
    cooldown=root/"tracking_retry.json"
    if cooldown.exists():
        last=json.loads(cooldown.read_text())
        if (pd.Timestamp(utcnow())-pd.Timestamp(last["attempted_at"])).total_seconds()<3*3600:
            return dict(pending=len(pending),downloaded=0,deferred=True,last_attempt=last)
    errors=[];downloaded=0
    try:
        from nba_api.stats.endpoints import leaguegamelog,boxscoreplayertrackv3
        by_season={}
        for path in pending:
            r=json.loads(path.read_text());by_season.setdefault(r["event"]["SEASON"],[]).append(r)
        for season,results in by_season.items():
            payload=leaguegamelog.LeagueGameLog(season=season,season_type_all_star="Regular Season",
                player_or_team_abbreviation="T",timeout=15).get_dict()
            write_json(run/"raw"/f"nba_gamelog_{season}.json",dict(fetched_at=utcnow().isoformat(),payload=payload))
            table=payload["resultSets"][0];g=pd.DataFrame(table["rowSet"],columns=table["headers"])
            for result in results:
                e=result["event"];day=pd.Timestamp(result["actual_tipoff"]).tz_convert("America/New_York").date()
                matches=g[(pd.to_datetime(g.GAME_DATE).dt.date==day)&g.TEAM_ID.isin([e["HOME_TEAM_ID"],e["AWAY_TEAM_ID"]])]
                ids=[gid for gid,q in matches.groupby("GAME_ID") if set(q.TEAM_ID)=={e["HOME_TEAM_ID"],e["AWAY_TEAM_ID"]}]
                if len(ids)!=1:
                    errors.append(dict(event=e["EVENT_ID"],error="NBA game id not resolved"));continue
                payload=boxscoreplayertrackv3.BoxScorePlayerTrackV3(game_id=ids[0],timeout=15).get_dict()
                write_json(run/"raw"/f"nba_tracking_{e['EVENT_ID']}.json",dict(fetched_at=utcnow().isoformat(),payload=payload))
                box=payload["boxScorePlayerTrack"]
                if str(box["gameId"])!=str(ids[0]):raise ValueError("tracking game id mismatch")
                players={}
                for side,tid in (("homeTeam",e["HOME_TEAM_ID"]),("awayTeam",e["AWAY_TEAM_ID"])):
                    if int(box[side]["teamId"])!=tid:raise ValueError("tracking team mismatch")
                    for a in box[side]["players"]:
                        st=a.get("statistics",{})
                        players[str(a["personId"])]= {k:st[k] for k in ("touches","passes","secondaryAssists","reboundChancesTotal") if k in st}
                if not any(v.get("touches",0)>0 for v in players.values()):raise ValueError("tracking response has no measured touches")
                write_json(root/"tracking"/f"{e['EVENT_ID']}.json",dict(nba_game_id=ids[0],players=players,collected_at=utcnow().isoformat()))
                downloaded+=1
    except Exception as exc:errors.append(dict(error=str(exc)))
    stamp=dict(attempted_at=utcnow().isoformat(),errors=errors)
    cooldown.write_text(json.dumps(stamp,indent=2))
    return dict(pending=len(pending),downloaded=downloaded,errors=errors)
