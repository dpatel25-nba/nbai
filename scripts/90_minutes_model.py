"""
Dedicated minutes / rotation model — the proven props lever.

Minutes projection is ~23% of player-points error (script 64). Our context
experiments showed one feature with real signal — `vacated` (a rotation teammate
ruled OUT -> this player absorbs minutes + usage). Here we build a focused minutes
model layering the validated levers and measure BOTH:

  1. minutes MAE (does the rotation model itself get better?)
  2. downstream points MAE (does better minutes flow through to scoring?)

Levers tested on top of the base recent-minutes set:
  - min_trend  = recent_min3 - recent_min10   (role trajectory: rising/falling)
  - vacated    = projected scoring of rotation teammates OUT this game
  - blowout    = |predicted game margin| (garbage time trims star minutes)

Usage: python scripts/90_minutes_model.py
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error

ROOT = Path(__file__).resolve().parents[1]
FEAT = ROOT / "data" / "parquet" / "props_features.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
SIM = ROOT / "data" / "features" / "sim_mode1_predictions.parquet"

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

    # vacated: projected scoring of rotation teammates OUT this game
    pg = pd.read_parquet(PG, columns=["SEASON", "SEASON_TYPE", "GAME_ID", "TEAM_ID",
                                      "PLAYER_ID", "MIN"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)]
    gp_count = pg.groupby(["TEAM_ID", "SEASON", "PLAYER_ID"]).size()
    rotation = defaultdict(list)
    for (tid, s, pid), n in gp_count.items():
        if n >= 20 and (pid, s) in proj_ppg:
            rotation[(tid, s)].append(pid)
    participants = defaultdict(set); team_of = {}
    for r in pg.itertuples():
        participants[(r.GAME_ID, r.TEAM_ID)].add(r.PLAYER_ID)
        team_of[(r.GAME_ID, r.PLAYER_ID)] = r.TEAM_ID
    vacated = {}
    for r in pg[["GAME_ID", "TEAM_ID", "SEASON"]].drop_duplicates().itertuples():
        rot = rotation.get((r.TEAM_ID, r.SEASON), [])
        present = participants[(r.GAME_ID, r.TEAM_ID)]
        vacated[(r.GAME_ID, r.TEAM_ID)] = sum(proj_ppg[(q, r.SEASON)] for q in rot if q not in present)

    sim = pd.read_parquet(SIM)
    blowout = {r.GAME_ID: abs(r.MU_HOME - r.MU_AWAY) for r in sim.itertuples()}

    df = pd.read_parquet(FEAT)
    df["TEAM_ID"] = [team_of.get((g, p)) for g, p in zip(df.GAME_ID, df.PLAYER_ID)]
    df = df.dropna(subset=["TEAM_ID"])
    df["vacated"] = [vacated.get((g, int(t)), 0.0) for g, t in zip(df.GAME_ID, df.TEAM_ID)]
    df["blowout"] = [blowout.get(g, 8.0) for g in df.GAME_ID]
    df["min_trend"] = df.recent_min3 - df.recent_min10   # role trajectory
    season = df.SEASON
    y_min = df.MIN.to_numpy(); y_pts = df.points.to_numpy()
    mask = season.isin(sorted(season.unique())[2:]).to_numpy()

    MBASE = ["proj_mpg", "recent_min3", "recent_min5", "recent_min10", "started_last",
             "min_std10", "rest"]
    PBASE = ["proj_pts36", "recent_pts5", "recent_pts10", "recent_p36", "opp_def", "home", "rest"]
    ADV = ["min_trend", "vacated", "blowout"]

    print(f"Minutes/rotation model — {mask.sum():,} test player-games\n")
    print(f"{'model':<34}{'minutes MAE':>13}{'points MAE':>13}")
    for label, madd in [("BASE recent-minutes set", []),
                        ("+ min_trend", ["min_trend"]),
                        ("+ min_trend + vacated", ["min_trend", "vacated"]),
                        ("+ min_trend + vacated + blowout", ADV)]:
        pm = walk_forward(df[MBASE + madd], y_min, season)
        df["pred_min"] = np.where(np.isnan(pm), df.proj_mpg, pm)
        pmae = mean_absolute_error(y_min[mask], df.pred_min.to_numpy()[mask])
        # downstream points (advanced minutes features also given to the points model)
        pts = walk_forward(df[["pred_min"] + PBASE + madd], y_pts, season)
        m2 = mask & ~np.isnan(pts)
        ptmae = mean_absolute_error(y_pts[m2], pts[m2])
        print(f"{label:<34}{pmae:>13.4f}{ptmae:>13.4f}")

    # feature importance for MINUTES (last season held out)
    seasons = sorted(season.unique()); cut = seasons[-1]
    tr, te = (season < cut).to_numpy(), (season == cut).to_numpy()
    cols = MBASE + ADV
    mdl = gbm(); mdl.fit(df[cols][tr], y_min[tr])
    r = permutation_importance(mdl, df[cols][te], y_min[te], scoring="neg_mean_absolute_error",
                               n_repeats=5, random_state=7)
    print("\nMinutes-model feature importance (MAE increase when shuffled):")
    for name, val in sorted(zip(cols, r.importances_mean), key=lambda t: -t[1]):
        print(f"  {name:<14} {val:+.4f}")


if __name__ == "__main__":
    main()
