"""
Own-team context, round 2 — correct recent-minutes staleness from roster change.

`vacated_min` (now in production) is tonight's LEVEL of freed-up minutes. But a
player's recent_min can be STALE: if his last games had teammates out (role
inflated) and tonight they're back, recent_min overstates tonight's role. Two new
signals built purely from own-team availability:

  vacated_delta = recent_avg(vacated_min over player's last games) - tonight_vacated_min
        >0: recent role was propped up by absences now resolved -> expect regression DOWN
        <0: teammates newly out vs his recent norm -> expect boost ABOVE recent form
  vacated_max   = projected minutes of the SINGLE biggest absent teammate tonight
        (losing your #1 option matters more than a bench absence — a star-out signal)

Tested on top of the production minutes set (which already has vacated_min/pos).
Usage: python scripts/92_team_context.py
"""

from __future__ import annotations

from collections import defaultdict, deque
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
    pmpg = project_rate(ps, "MPG")

    pg = pd.read_parquet(PG, columns=["SEASON", "SEASON_TYPE", "GAME_ID", "GAME_DATE",
                                      "TEAM_ID", "PLAYER_ID", "MIN"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)].copy()
    gp_count = pg.groupby(["TEAM_ID", "SEASON", "PLAYER_ID"]).size()
    rotation = defaultdict(list)
    for (tid, s, pid), n in gp_count.items():
        if n >= 20 and (pid, s) in pmpg:
            rotation[(tid, s)].append(pid)
    participants = defaultdict(set)
    for r in pg.itertuples():
        participants[(r.GAME_ID, r.TEAM_ID)].add(r.PLAYER_ID)
    vac_min, vac_max = {}, {}
    for r in pg[["GAME_ID", "TEAM_ID", "SEASON"]].drop_duplicates().itertuples():
        present = participants[(r.GAME_ID, r.TEAM_ID)]
        outs = [pmpg[(q, r.SEASON)] for q in rotation.get((r.TEAM_ID, r.SEASON), [])
                if q not in present]
        vac_min[(r.GAME_ID, r.TEAM_ID)] = sum(outs)
        vac_max[(r.GAME_ID, r.TEAM_ID)] = max(outs) if outs else 0.0

    # per-player recent vacated context (leakage-safe: past games only)
    pg = pg.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)
    recent = defaultdict(lambda: deque(maxlen=10))
    vdelta = {}
    for r in pg.itertuples():
        tonight = vac_min.get((r.GAME_ID, r.TEAM_ID), 0.0)
        h = recent[r.PLAYER_ID]
        vdelta[(r.GAME_ID, r.PLAYER_ID)] = (np.mean(h) - tonight) if h else 0.0
        h.append(tonight)

    df = pd.read_parquet(FEAT)
    tof = {}
    for r in pg.itertuples():
        tof[(r.GAME_ID, r.PLAYER_ID)] = r.TEAM_ID
    df["TEAM_ID"] = [tof.get((g, p)) for g, p in zip(df.GAME_ID, df.PLAYER_ID)]
    df = df.dropna(subset=["TEAM_ID"]).copy()
    df["vacated_delta"] = [vdelta.get((g, p), 0.0) for g, p in zip(df.GAME_ID, df.PLAYER_ID)]
    df["vacated_max"] = [vac_max.get((g, int(t)), 0.0) for g, t in zip(df.GAME_ID, df.TEAM_ID)]

    season = df.SEASON
    y_min = df.MIN.to_numpy(); y_pts = df.points.to_numpy()
    mask = season.isin(sorted(season.unique())[2:]).to_numpy()

    MPROD = ["proj_mpg", "recent_min3", "recent_min5", "recent_min10", "started_last",
             "min_std10", "rest", "vacated_min", "vacated_pos"]
    PBASE = ["proj_pts36", "recent_pts5", "recent_pts10", "recent_p36", "opp_def", "home", "rest"]

    print(f"Own-team context round 2 — {mask.sum():,} test player-games\n")
    print(f"{'minutes-model addition':<40}{'minutes MAE':>13}{'points MAE':>13}")
    for label, add in [("PRODUCTION (vacated_min+pos)", []),
                       ("+ vacated_delta", ["vacated_delta"]),
                       ("+ vacated_max", ["vacated_max"]),
                       ("+ vacated_delta + vacated_max", ["vacated_delta", "vacated_max"])]:
        pm = walk_forward(df[MPROD + add], y_min, season)
        df["pred_min"] = np.where(np.isnan(pm), df.proj_mpg, pm)
        pmae = mean_absolute_error(y_min[mask], df.pred_min.to_numpy()[mask])
        pts = walk_forward(df[["pred_min"] + PBASE + add], y_pts, season)
        m2 = mask & ~np.isnan(pts)
        print(f"{label:<40}{pmae:>13.4f}{mean_absolute_error(y_pts[m2], pts[m2]):>13.4f}")

    seasons = sorted(season.unique()); cut = seasons[-1]
    tr, te = (season < cut).to_numpy(), (season == cut).to_numpy()
    cols = MPROD + ["vacated_delta", "vacated_max"]
    mdl = gbm(); mdl.fit(df[cols][tr], y_min[tr])
    r = permutation_importance(mdl, df[cols][te], y_min[te], scoring="neg_mean_absolute_error",
                               n_repeats=5, random_state=7)
    print("\nMinutes importance (new features vs incumbents):")
    for name, val in sorted(zip(cols, r.importances_mean), key=lambda t: -t[1]):
        tag = " <-- new" if name in ("vacated_delta", "vacated_max") else ""
        print(f"  {name:<14} {val:+.4f}{tag}")


if __name__ == "__main__":
    main()
