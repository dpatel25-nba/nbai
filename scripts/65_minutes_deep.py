"""
Deep minutes projection: richer features, see how close we can get.

v1 model (MAE 4.99) used [proj_mpg, recent_min5, rest, b2b]. This engineers a much
richer set and tests what actually helps:
  - multiple recency windows (last 3 / 5 / 10)
  - minutes trend (rising/falling role) and volatility (consistency)
  - STARTER status (started last game / start-rate) -- from the box position field
  - BLOWOUT risk (|predicted margin| from the game model -> garbage time)
  - season ramp (player's game number in the season)

Walk-forward, GB, with permutation importance and an ablation vs v1.

Usage: python scripts/65_minutes_deep.py
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, r2_score

ROOT = Path(__file__).resolve().parents[1]
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
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
    return HistGradientBoostingRegressor(max_iter=500, learning_rate=0.05,
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
    proj_mpg = project_rate(pd.read_parquet(PS), "MPG")
    sim = pd.read_parquet(SIM)
    blowout = {r.GAME_ID: abs(r.MU_HOME - r.MU_AWAY) for r in sim.itertuples()}

    pg = pd.read_parquet(PG, columns=["GAME_ID", "GAME_DATE", "SEASON", "SEASON_TYPE",
                                      "PLAYER_ID", "MIN", "position"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)].copy()
    pg["started"] = pg.position.fillna("").str.len().gt(0).astype(int)
    pg = pg.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)

    hist = defaultdict(lambda: deque(maxlen=10))
    lastdate = {}; gnum = defaultdict(int); pseason = {}
    rows = []
    for r in pg.itertuples():
        pj = proj_mpg.get((r.PLAYER_ID, r.SEASON))
        # reset season game counter
        if pseason.get(r.PLAYER_ID) != r.SEASON:
            gnum[r.PLAYER_ID] = 0; pseason[r.PLAYER_ID] = r.SEASON
        h = hist[r.PLAYER_ID]
        if pj is not None and len(h) >= 3:
            mins = [e[0] for e in h]; starts = [e[1] for e in h]
            rest = (r.GAME_DATE - lastdate[r.PLAYER_ID]).days if r.PLAYER_ID in lastdate else 5
            rows.append({
                "SEASON": r.SEASON, "min": r.MIN,
                "recent_min3": np.mean(mins[-3:]), "recent_min5": np.mean(mins[-5:]),
                "recent_min10": np.mean(mins), "min_std10": np.std(mins),
                "min_trend": np.mean(mins[-3:]) - np.mean(mins),
                "started_last": starts[-1], "started_rate5": np.mean(starts[-5:]),
                "proj_mpg": pj, "rest": min(rest, 7), "b2b": int(rest == 1),
                "blowout": blowout.get(r.GAME_ID, 8.0), "gnum": gnum[r.PLAYER_ID],
            })
        hist[r.PLAYER_ID].append((r.MIN, r.started))
        lastdate[r.PLAYER_ID] = r.GAME_DATE; gnum[r.PLAYER_ID] += 1

    df = pd.DataFrame(rows)
    season = df.SEASON
    mask = (season.map({s: i for i, s in enumerate(sorted(season.unique()))}) >= 2).to_numpy()
    y = df["min"].to_numpy()
    ALL = ["recent_min3", "recent_min5", "recent_min10", "min_std10", "min_trend",
           "started_last", "started_rate5", "proj_mpg", "rest", "b2b", "blowout", "gnum"]

    print(f"Deep minutes projection — {mask.sum():,} test player-games\n")
    print("MODEL PROGRESSION (minutes MAE / R2):")
    for label, cols in [("v1 baseline (4 feats)", ["proj_mpg", "recent_min5", "rest", "b2b"]),
                        ("+ recency windows+trend", ["proj_mpg", "recent_min3", "recent_min5",
                            "recent_min10", "min_std10", "min_trend", "rest", "b2b"]),
                        ("+ starter status", ["proj_mpg", "recent_min3", "recent_min5", "recent_min10",
                            "min_std10", "min_trend", "started_last", "started_rate5", "rest", "b2b"]),
                        ("+ blowout + season ramp (full)", ALL)]:
        p = walk_forward(df[cols], y, season)
        print(f"  {label:<32} MAE {mean_absolute_error(y[mask], p[mask]):.3f}   "
              f"R2 {r2_score(y[mask], p[mask]):.3f}")

    seasons = sorted(season.unique()); cut = seasons[-2]
    tr, te = (season < cut).to_numpy(), (season >= cut).to_numpy()
    m = gbm(); m.fit(df[ALL][tr], y[tr])
    r = permutation_importance(m, df[ALL][te], y[te], scoring="neg_mean_absolute_error",
                               n_repeats=6, random_state=7)
    print("\nWHAT PREDICTS MINUTES (permutation importance):")
    for name, val in sorted(zip(ALL, r.importances_mean), key=lambda t: -t[1]):
        print(f"  {name:<16} {val:+.3f}  {'#'*int(max(val,0)*20)}")


if __name__ == "__main__":
    main()
