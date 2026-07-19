"""
Do tracking shot-diet features improve player-points props? (backtest)

Hypothesis: HOW a player scores (drives, catch&shoot vs pull-up, post/paint
touches, time of possession, pts/touch) is stickier than raw scoring, so a
projected shot-diet profile may add signal beyond proj_pts36 + recent form +
the hot-hand decomposition already in production.

Leakage-safe: each tracking feature is projected from PRIOR seasons (recency-
weighted, regressed to mean) — same Marcel engine as proj_pts36. Added to the
production points model; report MAE + importance.

Usage: python scripts/97_tracking_props.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error

ROOT = Path(__file__).resolve().parents[1]
FEAT = ROOT / "data" / "parquet" / "props_features.parquet"
TRK = ROOT / "data" / "parquet" / "player_tracking.parquet"

RECENCY = {1: 5.0, 2: 4.0, 3: 3.0}
K = 30.0   # phantom games for regression (tracking is per-game, lighter shrink)

# per-36 volume (shot diet) + as-is efficiency/role signals
RATE36 = ["DRIVES", "CATCH_SHOOT_FGA", "PULL_UP_FGA", "POST_TOUCHES", "PAINT_TOUCHES", "TOUCHES"]
ASIS = ["PTS_PER_TOUCH", "TIME_OF_POSS", "AVG_SEC_PER_TOUCH", "DRIVE_FG_PCT", "CATCH_SHOOT_EFG_PCT"]


def project(df, metric):
    order = {s: i for i, s in enumerate(sorted(df.SEASON.unique()))}
    inv = {i: s for s, i in order.items()}
    val = {(r.PLAYER_ID, r.SEASON): getattr(r, metric) for r in df.itertuples()}
    gp = {(r.PLAYER_ID, r.SEASON): r.GP for r in df.itertuples()}
    out = {}
    prior_cache = {}
    for r in df.itertuples():
        ti = order[r.SEASON]
        if ti == 0:
            continue
        if ti not in prior_cache:
            pri = df[df.SEASON.map(order) < ti]
            w = pri[metric].notna() & pri.GP.notna()
            prior_cache[ti] = np.average(pri.loc[w, metric], weights=pri.loc[w, "GP"]) if w.any() else np.nan
        pm = prior_cache[ti]
        num = den = 0.0
        for lag, wt in RECENCY.items():
            s = inv.get(ti - lag)
            v, g = val.get((r.PLAYER_ID, s)), gp.get((r.PLAYER_ID, s))
            if v is not None and g is not None and not (isinstance(v, float) and np.isnan(v)):
                num += wt * g * v; den += wt * g
        if den and not np.isnan(pm):
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
    trk = pd.read_parquet(TRK)
    trk = trk[trk.MIN.notna() & (trk.MIN > 0)].copy()
    for c in RATE36:
        if c in trk.columns:
            trk[c] = trk[c] / trk.MIN * 36   # per-36

    proj = {}
    for c in RATE36 + ASIS:
        if c in trk.columns:
            proj[c] = project(trk[["PLAYER_ID", "SEASON", "GP", "MIN", c]].rename(columns={c: c}), c)

    df = pd.read_parquet(FEAT)
    newcols = []
    for c, d in proj.items():
        col = f"trk_{c.lower()}"
        df[col] = [d.get((p, s), np.nan) for p, s in zip(df.PLAYER_ID, df.SEASON)]
        newcols.append(col)
    # players without a projected tracking profile (rookies etc.) -> median fill
    df[newcols] = df[newcols].fillna(df[newcols].median())

    season = df.SEASON
    y = df.points.to_numpy()
    mask = season.isin(sorted(season.unique())[2:]).to_numpy()

    mfeat = ["proj_mpg", "recent_min3", "recent_min5", "recent_min10", "started_last", "rest",
             "vacated_min", "vacated_pos", "vacated_delta", "load3", "own_missed3", "own_missed10"]
    pm = walk_forward(df[mfeat], df.MIN.to_numpy(), season)
    df["pred_min"] = np.where(np.isnan(pm), df.proj_mpg, pm)

    PPROD = ["pred_min", "proj_pts36", "recent_pts5", "recent_pts10", "recent_p36",
             "opp_def", "home", "rest", "vacated_min", "vacated_pos", "vacated_delta",
             "recent_fga36_5", "recent_fga36_10", "recent_ts5", "recent_ts10", "recent_ts_delta"]

    print(f"Tracking shot-diet on props — {mask.sum():,} test player-games\n")
    print(f"{'points model':<34}{'points MAE':>12}")
    for label, add in [("PRODUCTION", []), ("+ tracking shot-diet", newcols)]:
        pts = walk_forward(df[PPROD + add], y, season)
        m2 = mask & ~np.isnan(pts)
        print(f"{label:<34}{mean_absolute_error(y[m2], pts[m2]):>12.4f}")

    seasons = sorted(season.unique()); cut = seasons[-1]
    tr, te = (season < cut).to_numpy(), (season == cut).to_numpy()
    cols = PPROD + newcols
    mdl = gbm(); mdl.fit(df[cols][tr], y[tr])
    r = permutation_importance(mdl, df[cols][te], y[te], scoring="neg_mean_absolute_error",
                               n_repeats=5, random_state=7)
    print("\nTop tracking features by importance:")
    imp = sorted([(n, v) for n, v in zip(cols, r.importances_mean) if n in newcols], key=lambda t: -t[1])
    for n, v in imp:
        print(f"  {n:<24} {v:+.4f}")


if __name__ == "__main__":
    main()
