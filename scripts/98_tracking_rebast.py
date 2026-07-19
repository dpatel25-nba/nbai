"""
Do tracking OPPORTUNITY stats improve rebound / assist props? (backtest)

Points is saturated (script 97), but rebounds & assists are opportunity-driven,
and tracking measures opportunity directly — signals box scores never had:
  rebounds: OREB/DREB CHANCES (boards you were near), contest %
  assists:  POTENTIAL_AST, PASSES_MADE, SECONDARY_AST, AST_POINTS_CREATED
A great passer on a cold-shooting team logs few assists but many potential ones —
tracking sees the skill the box score hides.

Projected from prior seasons (leakage-safe), added to the production reb/ast models.
Usage: python scripts/98_tracking_rebast.py
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
K = 30.0

REB_TRK = ["OREB_CHANCES", "DREB_CHANCES", "OREB_CONTEST_PCT", "DREB_CONTEST_PCT"]
AST_TRK = ["POTENTIAL_AST", "PASSES_MADE", "SECONDARY_AST", "AST_POINTS_CREATED"]
RATE36 = {"OREB_CHANCES", "DREB_CHANCES", "POTENTIAL_AST", "PASSES_MADE",
          "SECONDARY_AST", "AST_POINTS_CREATED"}


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
            trk[c] = trk[c] / trk.MIN * 36

    df = pd.read_parquet(FEAT)
    for c in REB_TRK + AST_TRK:
        if c not in trk.columns:
            continue
        d = project(trk[["PLAYER_ID", "SEASON", "GP", c]], c)
        col = f"trk_{c.lower()}"
        df[col] = [d.get((p, s), np.nan) for p, s in zip(df.PLAYER_ID, df.SEASON)]
    reb_cols = [f"trk_{c.lower()}" for c in REB_TRK]
    ast_cols = [f"trk_{c.lower()}" for c in AST_TRK]
    df[reb_cols + ast_cols] = df[reb_cols + ast_cols].fillna(df[reb_cols + ast_cols].median())

    season = df.SEASON
    mask = season.isin(sorted(season.unique())[2:]).to_numpy()
    mfeat = ["proj_mpg", "recent_min3", "recent_min5", "recent_min10", "started_last", "rest",
             "vacated_min", "vacated_pos", "vacated_delta", "load3", "own_missed3", "own_missed10"]
    pm = walk_forward(df[mfeat], df.MIN.to_numpy(), season)
    df["pred_min"] = np.where(np.isnan(pm), df.proj_mpg, pm)
    vac = ["vacated_min", "vacated_pos", "vacated_delta"]

    specs = {
        "reb": (("proj_reb36", "recent_reb5", "recent_reb10", "recent_r36"), reb_cols),
        "ast": (("proj_ast36", "recent_ast5", "recent_ast10", "recent_a36"), ast_cols),
    }
    print(f"Tracking opportunity stats on reb/ast — {mask.sum():,} test player-games\n")
    for stat, ((prate, r5, r10, r36), tcols) in specs.items():
        base = ["pred_min", prate, r5, r10, r36, "opp_def", "home", "rest"] + vac
        print(f"=== {stat.upper()} ===")
        for label, add in [("PRODUCTION", []), ("+ tracking opportunity", tcols)]:
            pred = walk_forward(df[base + add], df[stat].to_numpy(), season)
            m2 = mask & ~np.isnan(pred)
            print(f"  {label:<26}{mean_absolute_error(df[stat].to_numpy()[m2], pred[m2]):.4f}")
        seasons = sorted(season.unique()); cut = seasons[-1]
        trn, te = (season < cut).to_numpy(), (season == cut).to_numpy()
        cols = base + tcols
        mdl = gbm(); mdl.fit(df[cols][trn], df[stat].to_numpy()[trn])
        r = permutation_importance(mdl, df[cols][te], df[stat].to_numpy()[te],
                                   scoring="neg_mean_absolute_error", n_repeats=5, random_state=7)
        for n, v in sorted([(n, v) for n, v in zip(cols, r.importances_mean) if n in tcols],
                           key=lambda t: -t[1]):
            print(f"     {n:<24} {v:+.4f}")
        print()


if __name__ == "__main__":
    main()
