"""
Clean test — is the opening-prop edge REAL or just vacated-hindsight?

Our projection uses who ACTUALLY played; the -12h opening line may not yet reflect
late scratches. So part of the opening-line "edge" could be us knowing news the
line doesn't. This splits the edge by whether the offense's team had a NEW absence
this game (a rotation player out who PLAYED the prior game = a late/surprise
scratch, the leak-prone case) vs a STABLE roster (everything knowable at -12h).

If the edge holds on STABLE-roster games -> real and realizable.
If it only shows in surprise-absence games -> partly the hindsight leak.

Usage: python scripts/118_props_clean_test.py
"""

from __future__ import annotations

import glob
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "props_odds"
PRED = ROOT / "data" / "features" / "props_predictions.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
BE = 52.38


def nmz(x):
    n = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
    for j in (" jr", " iii", " ii", " iv", ".", "'"):
        n = n.replace(j, "")
    return n.strip()


def parse_open():
    rows = []
    for f in glob.glob(str(RAW / "*_player_points_open.json")):
        gid = Path(f).stem.replace("_player_points_open", "")
        data = json.loads(Path(f).read_text()).get("data", {})
        agg = defaultdict(list)
        for bk in data.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk.get("key") == "player_points":
                    for o in mk.get("outcomes", []):
                        if o.get("name") == "Over":
                            agg[nmz(o.get("description"))].append(o.get("point"))
        for p, l in agg.items():
            if l:
                rows.append({"GAME_ID": gid, "pname": p, "open_line": float(np.median(l))})
    return pd.DataFrame(rows)


def team_new_absence():
    """per (GAME_ID, TEAM_ID): did a rotation player miss this game but play the prior one?"""
    pg = pd.read_parquet(PG, columns=["SEASON", "SEASON_TYPE", "GAME_ID", "GAME_DATE",
                                      "TEAM_ID", "PLAYER_ID", "MIN"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.SEASON.isin(["2023-24", "2024-25"]))]
    played = pg[pg.MIN > 0]
    gp = played.groupby(["TEAM_ID", "SEASON", "PLAYER_ID"]).size()
    rotation = {(t, s): set() for t, s in {(t, s) for t, s, _ in gp.index}}
    for (t, s, p), n in gp.items():
        if n >= 20:
            rotation[(t, s)].add(p)
    # team schedule + who played each game
    part = defaultdict(set)
    for r in played.itertuples():
        part[(r.GAME_ID, r.TEAM_ID)].add(r.PLAYER_ID)
    sched = defaultdict(list)
    for r in played[["GAME_ID", "TEAM_ID", "SEASON", "GAME_DATE"]].drop_duplicates().sort_values("GAME_DATE").itertuples():
        sched[(r.TEAM_ID, r.SEASON)].append(r.GAME_ID)
    new_abs = {}
    for (t, s), games in sched.items():
        rot = rotation.get((t, s), set())
        for i, gid in enumerate(games):
            present = part[(gid, t)]
            prior = part[(games[i - 1], t)] if i > 0 else set()
            # a rotation player out now who was IN the prior game = new/surprise absence
            new_abs[(gid, t)] = any((q not in present) and (q in prior) for q in rot)
    return new_abs


def main() -> None:
    op = parse_open()
    if not len(op):
        print("No opening prop data yet."); return
    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "PLAYER"])
    nm = {nmz(r.PLAYER): r.PLAYER_ID for r in ps[ps.SEASON.isin(["2023-24", "2024-25"])].itertuples()}
    op["PLAYER_ID"] = op.pname.map(nm)
    op = op.dropna(subset=["PLAYER_ID"]); op["PLAYER_ID"] = op.PLAYER_ID.astype(int)

    pred = pd.read_parquet(PRED)
    pred = pred[pred.SEASON.isin(["2023-24", "2024-25"])][["GAME_ID", "PLAYER_ID", "pred_points", "points"]]
    d = op.merge(pred, on=["GAME_ID", "PLAYER_ID"], how="inner").dropna(subset=["pred_points"])
    d = d[d.points != d.open_line].copy()

    # attach team + new-absence flag
    pg = pd.read_parquet(PG, columns=["GAME_ID", "PLAYER_ID", "TEAM_ID", "MIN"])
    tof = {(r.GAME_ID, r.PLAYER_ID): r.TEAM_ID for r in pg[pg.MIN > 0].itertuples()}
    d["TEAM_ID"] = [tof.get((g, p)) for g, p in zip(d.GAME_ID, d.PLAYER_ID)]
    d = d.dropna(subset=["TEAM_ID"])
    na = team_new_absence()
    d["surprise"] = [na.get((g, int(t)), False) for g, t in zip(d.GAME_ID, d.TEAM_ID)]

    d["over"] = d.pred_points > d.open_line
    d["won"] = (d.over & (d.points > d.open_line)) | (~d.over & (d.points < d.open_line))
    d["edge"] = (d.pred_points - d.open_line).abs()
    print(f"Clean test — {len(d):,} opening-line bets  "
          f"({d.surprise.mean()*100:.0f}% in surprise-absence games)\n")

    for name, sub in [("STABLE roster (clean, realizable)", d[~d.surprise]),
                      ("SURPRISE absence (possible hindsight)", d[d.surprise]),
                      ("ALL", d)]:
        print(f"{name}:")
        for lbl, q in [("all", 0.0), ("top 25% edge", 0.75), ("top 10% edge", 0.9)]:
            m = sub.edge >= sub.edge.quantile(q)
            wr = sub.won[m].mean() * 100
            print(f"    {lbl:<14} win {wr:5.2f}%  ({m.sum():,})  {'EDGE' if wr > BE else ''}")
        print()


if __name__ == "__main__":
    main()
