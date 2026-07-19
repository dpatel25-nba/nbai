"""
Player-props predictive-power study: what predicts a player's points in a game.

Same rigor as the game study: leakage-free features, walk-forward by season,
model comparison, permutation importance, and an incremental ablation.

Usage: python scripts/63_player_predictive_study.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
FEAT = ROOT / "data" / "parquet" / "player_game_features.parquet"
COLS = ["proj_pts36", "min", "recent_pts5", "recent_pts10", "recent_min5",
        "recent_p36_10", "opp_def", "home", "rest", "b2b"]


def walk_forward(make_model, X, y, season):
    seasons = sorted(season.unique())
    p = np.full(len(X), np.nan)
    for T in seasons[2:]:
        tr, te = (season < T).to_numpy(), (season == T).to_numpy()
        if te.sum() == 0:
            continue
        m = make_model(); m.fit(X[tr], y[tr])
        p[te] = m.predict(X[te])
    return p


def main() -> None:
    df = pd.read_parquet(FEAT)
    X = df[COLS]; y = df.points.to_numpy(); season = df.SEASON
    mask = (season.map({s: i for i, s in enumerate(sorted(season.unique()))}) >= 2).to_numpy()
    print(f"Player-props study — {mask.sum():,} test player-games (2015-16+)\n")

    ridge = lambda: make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    gbm = lambda: HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05,
                                                max_leaf_nodes=31, l2_regularization=1.0,
                                                random_state=7)

    print("MODEL COMPARISON (points MAE, lower better):")
    naive = (df.proj_pts36 * df["min"] / 36).to_numpy()
    print(f"  {'naive proj_rate x minutes':<28} {mean_absolute_error(y[mask], naive[mask]):.4f}")
    p_r = walk_forward(ridge, X, y, season)
    print(f"  {'ridge (all features)':<28} {mean_absolute_error(y[mask], p_r[mask]):.4f}")
    p_g = walk_forward(gbm, X, y, season)
    print(f"  {'grad boosting (all)':<28} {mean_absolute_error(y[mask], p_g[mask]):.4f}")

    # permutation importance (GB), train earlier, test last 2 seasons
    seasons = sorted(season.unique()); cut = seasons[-2]
    tr, te = (season < cut).to_numpy(), (season >= cut).to_numpy()
    m = gbm(); m.fit(X[tr], y[tr])
    r = permutation_importance(m, X[te], y[te], scoring="neg_mean_absolute_error",
                               n_repeats=6, random_state=7)
    imp = sorted(zip(COLS, r.importances_mean), key=lambda t: -t[1])
    print("\nPERMUTATION IMPORTANCE (points MAE increase when feature is shuffled):")
    for name, mean in imp:
        print(f"  {name:<16} {mean:+.4f}  {'#'*int(max(mean,0)*30)}")

    print("\nINCREMENTAL VALUE (GB walk-forward MAE):")
    groups = {
        "base: proj + minutes": ["proj_pts36", "min"],
        "+ recent form": ["proj_pts36", "min", "recent_pts5", "recent_pts10", "recent_min5", "recent_p36_10"],
        "+ opponent defense": ["proj_pts36", "min", "recent_pts5", "recent_pts10", "recent_min5", "recent_p36_10", "opp_def"],
        "+ rest / home": COLS,
    }
    for label, cols in groups.items():
        p = walk_forward(gbm, X[cols], y, season)
        print(f"  {label:<24} MAE {mean_absolute_error(y[mask], p[mask]):.4f}")


if __name__ == "__main__":
    main()
