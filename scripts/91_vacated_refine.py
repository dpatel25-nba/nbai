"""
Sharpen the `vacated` feature — which flavor best predicts minutes?

Candidates (all = load vacated by rotation teammates who are OUT this game):
  vacated_pts  projected SCORING of absent teammates      (current winner)
  vacated_min  projected MINUTES of absent teammates       (direct minutes redistribution)
  vacated_pos  projected minutes of absent SAME-POSITION teammates (positional replacement)

Each added to the base minutes model; report minutes MAE + downstream points MAE.
Winner gets wired into the production props engine.

Usage: python scripts/91_vacated_refine.py
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

ROOT = Path(__file__).resolve().parents[1]
FEAT = ROOT / "data" / "parquet" / "props_features.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"

RECENCY = {1: 5.0, 2: 4.0, 3: 3.0}
K = 1000.0


def project_rate(df, metric):
    order = {s: i for i, s in enumerate(sorted(df.SEASON.unique()))}
    inv = {i: s for s, i in order.items()}
    val = {(r.PLAYER_ID, r.SEASON): getattr(r, metric) for r in df.itertuples()}
    mn = {(r.PLAYER_ID, r.SEASON): r.MIN for r in df.itertuples()}
    out = {}
    for r in df.itertuples():
        ti = order[r.SEASON]
        if ti == 0:
            continue
        prior = df[df.SEASON.map(order) < ti]
        pm = np.average(prior[metric].dropna(), weights=prior.loc[prior[metric].notna(), "MIN"])
        num = den = 0.0
        for lag, w in RECENCY.items():
            s = inv.get(ti - lag)
            v, m = val.get((r.PLAYER_ID, s)), mn.get((r.PLAYER_ID, s))
            if v is not None and m is not None and not np.isnan(v):
                num += w * m * v; den += w * m
        if den:
            out[(r.PLAYER_ID, r.SEASON)] = (num + K * pm) / (den + K)
    return out


def bucket(pos):
    if not isinstance(pos, str):
        return "?"
    if "C" in pos:
        return "C"
    if "F" in pos:
        return "F"
    if "G" in pos:
        return "G"
    return "?"


def gbm():
    return HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05,
                                         max_leaf_nodes=31, l2_regularization=1.0, random_state=7)


def walk_forward(X, y, season):
    seasons = sorted(season.unique())
    p = np.full(len(X), np.nan)
    for T in seasons[2:]:
        tr, te = (season < T).to_numpy(), (season == T).to_numpy()
        if te.sum():
            m = gbm(); m.fit(X[tr], y[tr]); p[te] = m.predict(X[te])
    return p


def main() -> None:
    ps = pd.read_parquet(PS)
    ppg = project_rate(ps, "PTS_36")
    pmpg = project_rate(ps, "MPG")
    proj_ppg = {k: ppg[k] * pmpg.get(k, 24) / 36 for k in ppg if k in pmpg}
    pos_of = {(r.PLAYER_ID, r.SEASON): bucket(r.POS) for r in ps.itertuples()}

    pg = pd.read_parquet(PG, columns=["SEASON", "SEASON_TYPE", "GAME_ID", "TEAM_ID",
                                      "PLAYER_ID", "MIN"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)]
    gp_count = pg.groupby(["TEAM_ID", "SEASON", "PLAYER_ID"]).size()
    rotation = defaultdict(list)
    for (tid, s, pid), n in gp_count.items():
        if n >= 20 and (pid, s) in proj_ppg and (pid, s) in pmpg:
            rotation[(tid, s)].append(pid)
    participants = defaultdict(set); team_of = {}
    for r in pg.itertuples():
        participants[(r.GAME_ID, r.TEAM_ID)].add(r.PLAYER_ID)
        team_of[(r.GAME_ID, r.PLAYER_ID)] = r.TEAM_ID

    # per (game, team): list of absent rotation teammates (pid, proj_mpg, proj_ppg, pos)
    absent = {}
    v_pts = {}; v_min = {}
    for r in pg[["GAME_ID", "TEAM_ID", "SEASON"]].drop_duplicates().itertuples():
        rot = rotation.get((r.TEAM_ID, r.SEASON), [])
        present = participants[(r.GAME_ID, r.TEAM_ID)]
        out = [(q, pmpg[(q, r.SEASON)], proj_ppg[(q, r.SEASON)], pos_of.get((q, r.SEASON), "?"))
               for q in rot if q not in present]
        absent[(r.GAME_ID, r.TEAM_ID)] = out
        v_pts[(r.GAME_ID, r.TEAM_ID)] = sum(o[2] for o in out)
        v_min[(r.GAME_ID, r.TEAM_ID)] = sum(o[1] for o in out)

    df = pd.read_parquet(FEAT)
    df["TEAM_ID"] = [team_of.get((g, p)) for g, p in zip(df.GAME_ID, df.PLAYER_ID)]
    df = df.dropna(subset=["TEAM_ID"]).copy()
    df["SEASON_"] = df.SEASON
    df["vacated_pts"] = [v_pts.get((g, int(t)), 0.0) for g, t in zip(df.GAME_ID, df.TEAM_ID)]
    df["vacated_min"] = [v_min.get((g, int(t)), 0.0) for g, t in zip(df.GAME_ID, df.TEAM_ID)]
    # positional: minutes of absent teammates who share the focal player's position
    def vpos(gid, tid, pid, s):
        mypos = pos_of.get((pid, s), "?")
        return sum(o[1] for o in absent.get((gid, int(tid)), []) if o[3] == mypos and mypos != "?")
    df["vacated_pos"] = [vpos(g, t, p, s) for g, t, p, s in
                         zip(df.GAME_ID, df.TEAM_ID, df.PLAYER_ID, df.SEASON)]

    season = df.SEASON
    y_min = df.MIN.to_numpy(); y_pts = df.points.to_numpy()
    mask = season.isin(sorted(season.unique())[2:]).to_numpy()

    MBASE = ["proj_mpg", "recent_min3", "recent_min5", "recent_min10", "started_last",
             "min_std10", "rest"]
    PBASE = ["proj_pts36", "recent_pts5", "recent_pts10", "recent_p36", "opp_def", "home", "rest"]

    print(f"Vacated refinement — {mask.sum():,} test player-games\n")
    print(f"{'minutes-model addition':<40}{'minutes MAE':>13}{'points MAE':>13}")
    variants = [("(none — base)", []),
                ("+ vacated_pts", ["vacated_pts"]),
                ("+ vacated_min", ["vacated_min"]),
                ("+ vacated_pos", ["vacated_pos"]),
                ("+ vacated_min + vacated_pos", ["vacated_min", "vacated_pos"]),
                ("+ pts + min + pos", ["vacated_pts", "vacated_min", "vacated_pos"])]
    for label, add in variants:
        pm = walk_forward(df[MBASE + add], y_min, season)
        df["pred_min"] = np.where(np.isnan(pm), df.proj_mpg, pm)
        pmae = mean_absolute_error(y_min[mask], df.pred_min.to_numpy()[mask])
        pts = walk_forward(df[["pred_min"] + PBASE + add], y_pts, season)
        m2 = mask & ~np.isnan(pts)
        print(f"{label:<40}{pmae:>13.4f}{mean_absolute_error(y_pts[m2], pts[m2]):>13.4f}")


if __name__ == "__main__":
    main()
