"""
Hot-hand decomposition — split recent scoring into VOLUME vs EFFICIENCY.

`recent_pts` conflates two signals that behave differently out of sample:
  - shot VOLUME (usage: FGA/36) — sticky, role-driven, persists
  - shooting EFFICIENCY (TS%) — noisy, regresses hard toward the player's norm
A model given them separately can lean on volume and regress efficiency, instead
of treating a lucky-shooting stretch the same as a real usage bump.

New rate features (leakage-safe, last 5/10 games):
  recent_fga36_5/10  = field-goal attempts per 36 (usage proxy)
  recent_ts_5/10     = true-shooting % over the window (window-summed, stable)
  recent_ts_delta    = recent TS% minus the player's season TS% (hot/cold shooting)

Added to the points rate model; report MAE + importance vs. plain recent_pts.
Usage: python scripts/94_usage_efficiency.py
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
PG = ROOT / "data" / "parquet" / "player_games.parquet"


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


def ts(pts, fga, fta):
    denom = 2 * (fga + 0.44 * fta)
    return pts / denom if denom > 0 else np.nan


def main() -> None:
    pg = pd.read_parquet(PG, columns=["SEASON", "SEASON_TYPE", "GAME_ID", "GAME_DATE",
                                      "PLAYER_ID", "MIN", "points", "fieldGoalsAttempted",
                                      "freeThrowsAttempted"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)].copy()
    pg = pg.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)

    # season-to-date TS baseline per (player, season) for the hot/cold delta
    season_ts = defaultdict(lambda: [0.0, 0.0, 0.0])  # pts, fga, fta accumulators
    hist = defaultdict(lambda: deque(maxlen=10))       # (min, pts, fga, fta)
    feat = {}
    for r in pg.itertuples():
        h = hist[r.PLAYER_ID]
        acc = season_ts[(r.PLAYER_ID, r.SEASON)]
        if len(h) >= 3:
            M = np.array([e[0] for e in h]); P = np.array([e[1] for e in h])
            FGA = np.array([e[2] for e in h]); FTA = np.array([e[3] for e in h])
            fga36_5 = FGA[-5:].sum() / M[-5:].sum() * 36
            fga36_10 = FGA.sum() / M.sum() * 36
            ts5 = ts(P[-5:].sum(), FGA[-5:].sum(), FTA[-5:].sum())
            ts10 = ts(P.sum(), FGA.sum(), FTA.sum())
            sts = ts(acc[0], acc[1], acc[2]) if acc[1] > 0 else ts10
            feat[(r.GAME_ID, r.PLAYER_ID)] = (fga36_5, fga36_10, ts5, ts10,
                                              (ts10 - sts) if not np.isnan(ts10) and not np.isnan(sts) else 0.0)
        hist[r.PLAYER_ID].append((r.MIN, r.points, r.fieldGoalsAttempted, r.freeThrowsAttempted))
        acc[0] += r.points; acc[1] += r.fieldGoalsAttempted; acc[2] += r.freeThrowsAttempted

    df = pd.read_parquet(FEAT)
    vals = np.array([feat.get((g, p), (np.nan,) * 5)
                     for g, p in zip(df.GAME_ID, df.PLAYER_ID)])
    for i, name in enumerate(["recent_fga36_5", "recent_fga36_10", "recent_ts5",
                              "recent_ts10", "recent_ts_delta"]):
        df[name] = vals[:, i]
    df = df.dropna(subset=["recent_fga36_10", "recent_ts10"]).copy()

    season = df.SEASON
    y = df.points.to_numpy()
    mask = season.isin(sorted(season.unique())[2:]).to_numpy()

    # production pred_min
    mfeat = ["proj_mpg", "recent_min3", "recent_min5", "recent_min10", "started_last", "rest",
             "vacated_min", "vacated_pos", "vacated_delta", "load3", "own_missed3", "own_missed10"]
    pm = walk_forward(df[mfeat], df.MIN.to_numpy(), season)
    df["pred_min"] = np.where(np.isnan(pm), df.proj_mpg, pm)

    PBASE = ["pred_min", "proj_pts36", "recent_pts5", "recent_pts10", "recent_p36",
             "opp_def", "home", "rest", "vacated_min", "vacated_pos", "vacated_delta"]
    NEW = ["recent_fga36_5", "recent_fga36_10", "recent_ts5", "recent_ts10", "recent_ts_delta"]

    print(f"Hot-hand decomposition — {mask.sum():,} test player-games\n")
    print(f"{'points rate model':<34}{'points MAE':>12}")
    for label, add in [("PRODUCTION rate features", []),
                       ("+ usage (fga36)", ["recent_fga36_5", "recent_fga36_10"]),
                       ("+ efficiency (ts)", ["recent_ts5", "recent_ts10", "recent_ts_delta"]),
                       ("+ usage + efficiency", NEW)]:
        pts = walk_forward(df[PBASE + add], y, season)
        m2 = mask & ~np.isnan(pts)
        print(f"{label:<34}{mean_absolute_error(y[m2], pts[m2]):>12.4f}")

    seasons = sorted(season.unique()); cut = seasons[-1]
    tr, te = (season < cut).to_numpy(), (season == cut).to_numpy()
    cols = PBASE + NEW
    mdl = gbm(); mdl.fit(df[cols][tr], y[tr])
    r = permutation_importance(mdl, df[cols][te], y[te], scoring="neg_mean_absolute_error",
                               n_repeats=5, random_state=7)
    print("\nPoints importance (new features flagged):")
    for name, val in sorted(zip(cols, r.importances_mean), key=lambda t: -t[1])[:10]:
        print(f"  {name:<18} {val:+.4f}{'  <-- new' if name in NEW else ''}")


if __name__ == "__main__":
    main()
