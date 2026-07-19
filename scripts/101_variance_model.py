"""
Predict the DISTRIBUTION, not the mean — a learned variance model for props.

The mean is saturated, but for betting the edge is VARIANCE: a steady 20-ppg
rim-runner and a boom-or-bust 20-ppg gunner need different over/under prices.
Our engine uses a crude constant-dispersion interval (var ∝ mean). Test whether
we can PREDICT each game's scoring variance from features — role, minutes
volatility, shot diet (3-point & pull-up volume are bimodal → high variance).

Target = squared residual of the points projection. Fit a GBM for conditional
variance, walk-forward. Evaluate with Gaussian NLL (proper scoring rule) and
interval coverage vs. the constant-dispersion baseline. Lower NLL = sharper,
better-calibrated distribution = real betting edge even at the same mean.

Usage: python scripts/101_variance_model.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

ROOT = Path(__file__).resolve().parents[1]
FEAT = ROOT / "data" / "parquet" / "props_features.parquet"
PRED = ROOT / "data" / "features" / "props_predictions.parquet"
TRK = ROOT / "data" / "parquet" / "player_tracking.parquet"

RECENCY = {1: 5.0, 2: 4.0, 3: 3.0}
KREG = 30.0


def project(df, metric):
    order = {s: i for i, s in enumerate(sorted(df.SEASON.unique()))}
    inv = {i: s for s, i in order.items()}
    val = {(r.PLAYER_ID, r.SEASON): getattr(r, metric) for r in df.itertuples()}
    gp = {(r.PLAYER_ID, r.SEASON): r.GP for r in df.itertuples()}
    out, cache = {}, {}
    for r in df.itertuples():
        ti = order[r.SEASON]
        if ti == 0:
            continue
        if ti not in cache:
            pri = df[df.SEASON.map(order) < ti]
            w = pri[metric].notna() & pri.GP.notna()
            cache[ti] = np.average(pri.loc[w, metric], weights=pri.loc[w, "GP"]) if w.any() else np.nan
        pm = cache[ti]
        num = den = 0.0
        for lag, wt in RECENCY.items():
            s = inv.get(ti - lag)
            v, g = val.get((r.PLAYER_ID, s)), gp.get((r.PLAYER_ID, s))
            if v is not None and g is not None and not (isinstance(v, float) and np.isnan(v)):
                num += wt * g * v; den += wt * g
        if den and not np.isnan(pm):
            out[(r.PLAYER_ID, r.SEASON)] = (num + KREG * pm) / (den + KREG)
    return out


def gbm():
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05,
                                         max_leaf_nodes=31, l2_regularization=1.0, random_state=7)


def nll(resid, var):
    var = np.clip(var, 1.0, None)
    return float(np.mean(0.5 * np.log(2 * np.pi * var) + resid ** 2 / (2 * var)))


def main() -> None:
    df = pd.read_parquet(FEAT)
    pred = pd.read_parquet(PRED)[["SEASON", "PLAYER_ID", "GAME_ID", "pred_points", "pred_min"]]
    df = df.merge(pred, on=["SEASON", "PLAYER_ID", "GAME_ID"], how="inner")
    df = df[df.pred_points.notna()].copy()

    # shot-diet volatility drivers (projected, leakage-safe): 3PA & pull-up volume
    trk = pd.read_parquet(TRK)
    trk = trk[trk.MIN.notna() & (trk.MIN > 0)].copy()
    trk["fg3a36"] = (trk.get("CATCH_SHOOT_FG3A", 0).fillna(0) +
                     trk.get("PULL_UP_FG3A", 0).fillna(0)) / trk.MIN * 36
    trk["pullup36"] = trk.get("PULL_UP_FGA", 0).fillna(0) / trk.MIN * 36
    for c in ("fg3a36", "pullup36"):
        d = project(trk[["PLAYER_ID", "SEASON", "GP", c]], c)
        df[c] = [d.get((p, s), np.nan) for p, s in zip(df.PLAYER_ID, df.SEASON)]
    df[["fg3a36", "pullup36"]] = df[["fg3a36", "pullup36"]].fillna(df[["fg3a36", "pullup36"]].median())

    df["resid"] = df.points - df.pred_points
    df["sq"] = df.resid ** 2
    season = df.SEASON
    seasons = sorted(season.unique())
    test = season.isin(seasons[2:]).to_numpy()

    VFEAT = ["pred_points", "pred_min", "min_std10", "recent_pts10", "recent_ts_delta",
             "proj_pts36", "fg3a36", "pullup36", "home", "rest"]

    # --- baseline: constant dispersion (var = disp * pred), disp fit per train split ---
    # --- model: GBM predicting squared residual (conditional variance) ---
    var_base = np.full(len(df), np.nan)
    var_model = np.full(len(df), np.nan)
    X = df[VFEAT].to_numpy(); sq = df.sq.to_numpy(); pp = df.pred_points.clip(lower=0.1).to_numpy()
    for T in seasons[2:]:
        tr, te = (season < T).to_numpy(), (season == T).to_numpy()
        if not te.sum():
            continue
        disp = np.mean(sq[tr] / pp[tr])          # dispersion factor from train
        var_base[te] = disp * pp[te]
        m = gbm(); m.fit(X[tr], sq[tr])          # learn conditional variance
        var_model[te] = np.clip(m.predict(X[te]), 1.0, None)

    resid = df.resid.to_numpy()
    m = test & ~np.isnan(var_model)
    print(f"Variance model — {m.sum():,} test player-games\n")
    print(f"{'method':<28}{'Gaussian NLL':>14}{'80% cover':>12}{'90% cover':>12}")
    for name, var in [("constant dispersion", var_base), ("learned variance", var_model)]:
        sd = np.sqrt(var[m])
        c80 = np.mean(np.abs(resid[m]) <= 1.2816 * sd)
        c90 = np.mean(np.abs(resid[m]) <= 1.6449 * sd)
        print(f"{name:<28}{nll(resid[m], var[m]):>14.4f}{c80*100:>11.1f}%{c90*100:>11.1f}%")

    # how much does predicted variance actually vary? (is there real signal to exploit?)
    sdm = np.sqrt(var_model[m])
    lo, hi = np.percentile(sdm, [10, 90])
    print(f"\nPredicted SD spread: p10={lo:.1f} … p90={hi:.1f} pts "
          f"(constant baseline SD≈{np.sqrt(np.mean(var_base[m])):.1f})")
    # actual |resid| in the model's low-var vs high-var buckets (does it separate?)
    q = np.quantile(sdm, [1/3, 2/3])
    for lbl, lo_, hi_ in [("low-var 3rd", -np.inf, q[0]), ("mid", q[0], q[1]), ("high-var 3rd", q[1], np.inf)]:
        b = (sdm > lo_) & (sdm <= hi_)
        print(f"  {lbl:<14} predicted SD {sdm[b].mean():.1f} → actual |resid| {np.abs(resid[m][b]).mean():.1f}")


if __name__ == "__main__":
    main()
