"""
Deep dive: minutes — the dominant driver of player scoring.

1. How predictable are a player's minutes for a game? (leakage-free model)
2. What predicts minutes, and how much is irreducible (foul trouble, blowouts)?
3. THE honest test: our player-points studies used ACTUAL minutes. A real pre-game
   props model must PROJECT minutes. How much worse is points prediction when we
   use projected minutes instead of actual? = the true difficulty + the value of a
   rotation model.

Usage: python scripts/64_minutes_study.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, r2_score

ROOT = Path(__file__).resolve().parents[1]
FEAT = ROOT / "data" / "parquet" / "player_game_features.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"

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
    df = pd.read_parquet(FEAT)
    ps = pd.read_parquet(PS)
    proj_mpg = project_rate(ps, "MPG")
    df["proj_mpg"] = [proj_mpg.get((p, s), np.nan) for p, s in zip(df.PLAYER_ID, df.SEASON)]
    df = df.dropna(subset=["proj_mpg"]).reset_index(drop=True)
    season = df.SEASON
    mask = (season.map({s: i for i, s in enumerate(sorted(season.unique()))}) >= 2).to_numpy()

    # --- 1. minutes predictability ---
    MFEAT = ["proj_mpg", "recent_min5", "rest", "b2b"]
    pred_min = walk_forward(df[MFEAT], df["min"].to_numpy(), season)
    ym = df["min"].to_numpy()
    print(f"Minutes prediction ({mask.sum():,} test player-games):")
    print(f"  MAE {mean_absolute_error(ym[mask], pred_min[mask]):.2f} min | "
          f"R2 {r2_score(ym[mask], pred_min[mask]):.3f}")
    print(f"  (context: naive 'use projected MPG' MAE "
          f"{mean_absolute_error(ym[mask], df['proj_mpg'].to_numpy()[mask]):.2f})")

    seasons = sorted(season.unique()); cut = seasons[-2]
    tr, te = (season < cut).to_numpy(), (season >= cut).to_numpy()
    m = gbm(); m.fit(df[MFEAT][tr], ym[tr])
    r = permutation_importance(m, df[MFEAT][te], ym[te], scoring="neg_mean_absolute_error",
                               n_repeats=6, random_state=7)
    print("  what predicts minutes:")
    for name, val in sorted(zip(MFEAT, r.importances_mean), key=lambda t: -t[1]):
        print(f"    {name:<14} {val:+.3f}")

    # --- 2. the honest cost: points with ACTUAL vs PROJECTED minutes ---
    PFEAT = ["proj_pts36", "min", "recent_pts5", "recent_pts10", "recent_min5",
             "recent_p36_10", "opp_def", "home", "rest", "b2b"]
    y = df.points.to_numpy()
    p_actual = walk_forward(df[PFEAT], y, season)

    dfp = df.copy()
    dfp["min"] = np.where(np.isnan(pred_min), df["proj_mpg"], pred_min)  # projected minutes
    p_proj = walk_forward(dfp[PFEAT], y, season)

    print("\nPoints prediction — actual vs projected minutes:")
    print(f"  with ACTUAL minutes (idealized) MAE {mean_absolute_error(y[mask], p_actual[mask]):.4f}")
    print(f"  with PROJECTED minutes (real)   MAE {mean_absolute_error(y[mask], p_proj[mask]):.4f}")
    gap = mean_absolute_error(y[mask], p_proj[mask]) - mean_absolute_error(y[mask], p_actual[mask])
    print(f"  --> minutes uncertainty costs {gap:.3f} pts of MAE "
          f"({gap / mean_absolute_error(y[mask], p_actual[mask]) * 100:.0f}% of the error)")

    # --- 3. where minutes go wrong: how much is blowouts / low-minute chaos ---
    err = np.abs(ym - pred_min)
    print("\nMinutes error by role (are stars predictable, role players chaotic?):")
    for lo, hi, lab in [(0, 15, "bench (<15 proj)"), (15, 28, "rotation (15-28)"), (28, 48, "starters (28+)")]:
        sel = mask & (df.proj_mpg.to_numpy() >= lo) & (df.proj_mpg.to_numpy() < hi)
        print(f"  {lab:<20} minutes MAE {err[sel].mean():.2f}  (n={sel.sum():,})")


if __name__ == "__main__":
    main()
