"""
Player-props engine + honest end-to-end backtest.

Applies everything the studies found:
  1. project minutes (GB) — recent games dominate,
  2. project PTS/REB/AST (GB, which beat linear) from projected minutes + recent
     form + context,
  3. turn each into a calibrated distribution (over-dispersed count -> 80% band).

Honest: uses PROJECTED minutes (not actual), so the numbers reflect real pre-game
difficulty. Reports MAE + interval coverage per stat vs. a naive baseline.

Usage: python scripts/71_props_engine.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

ROOT = Path(__file__).resolve().parents[1]
FEAT = ROOT / "data" / "parquet" / "props_features.parquet"
OUT = ROOT / "data" / "features" / "props_predictions.parquet"


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


def coverage(pred, actual, mask):
    e = np.clip(pred[mask], 0.1, None); y = actual[mask]
    disp = np.mean((y - pred[mask]) ** 2 / e)
    sd = np.sqrt(disp * e)
    return np.mean(np.abs(y - pred[mask]) <= 1.2816 * sd)


def main() -> None:
    df = pd.read_parquet(FEAT)
    season = df.SEASON
    mask = (season.map({s: i for i, s in enumerate(sorted(season.unique()))}) >= 2).to_numpy()

    # 1) minutes — includes the validated `vacated` levers (teammate absence -> more
    #    minutes; scripts 90/91): minutes MAE 4.87 -> 4.78, best of every feature tried.
    mfeat = ["proj_mpg", "recent_min3", "recent_min5", "recent_min10", "started_last", "rest",
             "vacated_min", "vacated_pos", "vacated_delta",
             "load3", "own_missed3", "own_missed10"]   # own availability + fatigue (script 93)
    pmin = walk_forward(df[mfeat], df.MIN.to_numpy(), season)
    df["pred_min"] = np.where(np.isnan(pmin), df.proj_mpg, pmin)
    print(f"Props engine — {mask.sum():,} test player-games (honest: projected minutes)\n")
    print(f"minutes MAE {mean_absolute_error(df.MIN.to_numpy()[mask], df.pred_min.to_numpy()[mask]):.2f}\n")

    # 2) stats
    specs = {
        "points": ("proj_pts36", "recent_pts5", "recent_pts10", "recent_p36"),
        "reb":    ("proj_reb36", "recent_reb5", "recent_reb10", "recent_r36"),
        "ast":    ("proj_ast36", "recent_ast5", "recent_ast10", "recent_a36"),
    }
    # vacated helps the RATE too, not just minutes: teammates out -> usage spike
    # (points 4.598 -> 4.568). Both channels validated (script 92 + rate test).
    vac = ["vacated_min", "vacated_pos", "vacated_delta"]
    # points gets the hot-hand decomposition too (usage vs efficiency, script 94)
    usage_eff = {"points": ["recent_fga36_5", "recent_fga36_10", "recent_ts5",
                            "recent_ts10", "recent_ts_delta"]}
    print(f"{'stat':<8}{'engine MAE':>12}{'naive MAE':>12}{'80% cover':>12}")
    for stat, (prate, r5, r10, r36) in specs.items():
        feats = (["pred_min", prate, r5, r10, r36, "opp_def", "home", "rest"]
                 + vac + usage_eff.get(stat, []))
        pred = walk_forward(df[feats], df[stat].to_numpy(), season)
        naive = df[prate].to_numpy() * df.pred_min.to_numpy() / 36.0
        df[f"pred_{stat}"] = pred
        eng = mean_absolute_error(df[stat].to_numpy()[mask], pred[mask])
        nai = mean_absolute_error(df[stat].to_numpy()[mask], naive[mask])
        cov = coverage(pred, df[stat].to_numpy(), mask)
        print(f"{stat:<8}{eng:>12.3f}{nai:>12.3f}{cov*100:>11.1f}%")

    df[["SEASON", "PLAYER_ID", "GAME_ID", "MIN", "points", "reb", "ast",
        "pred_min", "pred_points", "pred_reb", "pred_ast"]].to_parquet(OUT, index=False)

    # face check
    for pid, name in [(201939, "Stephen Curry"), (203999, "Nikola Jokic")]:
        s = df[(df.PLAYER_ID == pid) & mask]
        if len(s):
            print(f"\n{name} — projected vs actual (per game, test seasons):")
            g = s.groupby("SEASON").agg(
                GP=("GAME_ID", "size"),
                pMIN=("pred_min", "mean"), aMIN=("MIN", "mean"),
                pPTS=("pred_points", "mean"), aPTS=("points", "mean"),
                pREB=("pred_reb", "mean"), aREB=("reb", "mean"),
                pAST=("pred_ast", "mean"), aAST=("ast", "mean"))
            print(g.round(1).tail(4).to_string())
    print(f"\nSaved -> {OUT}")


if __name__ == "__main__":
    main()
