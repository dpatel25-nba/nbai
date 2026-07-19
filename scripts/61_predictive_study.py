"""
Predictive-power study: which stats and which models actually predict games.

Rigorous, leakage-free:
  - features are pre-game snapshots (built by 60_build_game_features.py)
  - walk-forward validation: for each test season, train only on prior seasons
  - models compared: Elo-only logistic, full logistic, gradient boosting
  - permutation importance ranks each feature by how much log-loss worsens when
    that feature (alone) is shuffled on the held-out set
  - incremental ablation: what each feature GROUP adds over Elo alone

Answers: what has predictive power, and does a fancy model beat a simple one.

Usage: python scripts/61_predictive_study.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
FEAT = ROOT / "data" / "parquet" / "game_features.parquet"


def build_features(df):
    f = pd.DataFrame({
        "elo_diff": df.H_elo - df.A_elo,
        "net_diff": df.H_net - df.A_net,
        "form_margin_diff": df.H_form_margin - df.A_form_margin,
        "form_win_diff": df.H_form_win - df.A_form_win,
        "efg_diff": df.H_efg - df.A_efg,
        "efg_def_diff": df.A_efg_def - df.H_efg_def,   # + = home defends shooting better
        "pace_sum": df.H_pace + df.A_pace,
        "rest_diff": df.H_rest - df.A_rest,
        "b2b_diff": df.A_b2b - df.H_b2b,               # + = away on a back-to-back (helps home)
        "dens7_diff": df.A_dens7 - df.H_dens7,
    })
    return f


def walk_forward(make_model, X, y, season):
    seasons = sorted(season.unique())
    p = np.full(len(X), np.nan)
    for T in seasons[2:]:                     # test 2015-16 onward
        tr, te = (season < T).to_numpy(), (season == T).to_numpy()
        if te.sum() == 0:
            continue
        m = make_model(); m.fit(X[tr], y[tr])
        p[te] = m.predict_proba(X[te])[:, 1]
    return p


def score(p, y, mask, label):
    p, yy = np.clip(p[mask], 1e-6, 1 - 1e-6), y[mask]
    acc = ((p > 0.5).astype(int) == yy).mean()
    ll = log_loss(yy, p)
    br = np.mean((p - yy) ** 2)
    print(f"  {label:<28} acc {acc:.3f}  logloss {ll:.4f}  brier {br:.4f}")
    return ll


def main() -> None:
    df = pd.read_parquet(FEAT).dropna().reset_index(drop=True)
    X = build_features(df)
    y = df.HOME_WIN.to_numpy()
    season = df.SEASON

    logit = lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, C=1.0))
    gbm = lambda: HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05,
                                                 max_leaf_nodes=15, l2_regularization=1.0,
                                                 random_state=7)

    mask = (season.map({s: i for i, s in enumerate(sorted(season.unique()))}) >= 2).to_numpy()
    print(f"Walk-forward study — {mask.sum():,} test games (2015-16+)\n")
    print("MODEL COMPARISON:")
    p_elo = walk_forward(logit, X[["elo_diff"]], y, season)
    score(p_elo, y, mask, "Elo only (logistic)")
    p_lr = walk_forward(logit, X, y, season)
    score(p_lr, y, mask, "All features (logistic)")
    p_gb = walk_forward(gbm, X, y, season)
    score(p_gb, y, mask, "All features (grad boosting)")

    # --- permutation importance (gradient boosting), train<=2 seasons back, test last 2 ---
    seasons = sorted(season.unique())
    cut = seasons[-2]
    tr, te = (season < cut).to_numpy(), (season >= cut).to_numpy()
    m = gbm(); m.fit(X[tr], y[tr])
    r = permutation_importance(m, X[te], y[te], scoring="neg_log_loss",
                               n_repeats=8, random_state=7)
    imp = sorted(zip(X.columns, r.importances_mean, r.importances_std),
                 key=lambda t: -t[1])
    print("\nPERMUTATION IMPORTANCE (log-loss increase when feature is shuffled):")
    for name, mean, std in imp:
        bar = "#" * int(max(mean, 0) * 2000)
        print(f"  {name:<20} {mean:+.4f} ± {std:.4f}  {bar}")

    # --- incremental ablation over Elo ---
    print("\nINCREMENTAL VALUE over Elo (logistic, walk-forward log-loss):")
    groups = {
        "elo_diff": ["elo_diff"],
        "+ efficiency/form": ["elo_diff", "net_diff", "form_margin_diff", "form_win_diff"],
        "+ shooting (eFG)": ["elo_diff", "net_diff", "form_margin_diff", "form_win_diff", "efg_diff", "efg_def_diff"],
        "+ rest/schedule": list(X.columns),
    }
    for label, cols in groups.items():
        p = walk_forward(logit, X[cols], y, season)
        pc, yy = np.clip(p[mask], 1e-6, 1 - 1e-6), y[mask]
        print(f"  {label:<22} logloss {log_loss(yy, pc):.4f}")


if __name__ == "__main__":
    main()
