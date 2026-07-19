"""
Does the player-specific defense a scorer FACES predict his scoring? (backtest)

Our earlier props studies said team-level opponent defense is too coarse to move
player points. This tests the orthogonal signal the matchup data unlocks: the
possession-weighted PRIOR-SEASON defensive skill of the specific defenders who
guarded the player in each game.

  def_faced[game, offender] = Σ(partial_poss · defender_skill_{T-1}) / Σ partial_poss

Leakage discipline: a defender's skill is taken from the season BEFORE the game
(never the current one), and def_faced could in principle be predicted from the
opponent's roster — so this is a fair "if we knew the matchup" upper bound. If it
doesn't help even idealized, the deployable version won't.

Walk-forward GB on player points, base vs. +def_faced. Reports MAE + importance.
Usage: python scripts/88_defense_faced_study.py
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error

ROOT = Path(__file__).resolve().parents[1]
FEAT = ROOT / "data" / "parquet" / "props_features.parquet"
MU = ROOT / "data" / "parquet" / "matchups.parquet"
DQ = ROOT / "data" / "parquet" / "defender_quality_v2.parquet"


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
    order_all = ["2013-14", "2014-15", "2015-16", "2016-17", "2017-18", "2018-19",
                 "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
    prev = {s: order_all[i - 1] for i, s in enumerate(order_all) if i}

    # defender skill by (id, season) -> prior-season lookup
    dq = pd.read_parquet(DQ)
    skill = {(r.PLAYER_ID, r.SEASON): r.skill for r in dq.itertuples()}

    # def_faced per (game, offender): weighted avg of guards' PRIOR-season skill
    mu = pd.read_parquet(MU)
    mu = mu[mu.SEASON_TYPE == "Regular Season"]
    num = defaultdict(float); den = defaultdict(float); cov = defaultdict(float)
    for r in mu.itertuples():
        ps = prev.get(r.SEASON)
        s = skill.get((r.DEF_ID, ps))
        key = (r.GAME_ID, r.OFF_ID)
        den[key] += r.partial_poss
        if s is not None:
            num[key] += r.partial_poss * s
            cov[key] += r.partial_poss
    def_faced = {k: num[k] / cov[k] for k in num if cov[k] > 0}
    # coverage: fraction of guarded possessions with a rated (prior-season) defender
    coverage = {k: cov[k] / den[k] for k in den if den[k] > 0}

    df = pd.read_parquet(FEAT)
    df["def_faced"] = [def_faced.get((g, p), np.nan) for g, p in zip(df.GAME_ID, df.PLAYER_ID)]
    df["def_cov"] = [coverage.get((g, p), 0.0) for g, p in zip(df.GAME_ID, df.PLAYER_ID)]
    # keep rows with a real matchup signal (well-covered)
    df = df[df.def_faced.notna() & (df.def_cov >= 0.5)].copy()
    season = df.SEASON
    y = df.points.to_numpy()

    MBASE = ["proj_mpg", "recent_min3", "recent_min5", "recent_min10", "started_last", "rest"]
    PBASE = ["proj_pts36", "recent_pts5", "recent_pts10", "recent_p36", "opp_def", "home", "rest"]

    seasons = sorted(season.unique())
    test_mask = season.isin(seasons[2:]).to_numpy()
    print(f"def_faced study — {len(df):,} player-games w/ matchup signal, "
          f"seasons {seasons[0]}…{seasons[-1]}")
    print(f"  test rows (walk-forward): {test_mask.sum():,}\n")

    pmin = walk_forward(df[MBASE], df.MIN.to_numpy(), season)
    df["pred_min"] = np.where(np.isnan(pmin), df.proj_mpg, pmin)
    for label, add in [("BASE (minutes+form+team opp_def)", []),
                       ("+ def_faced (player-specific matchup)", ["def_faced"])]:
        pts = walk_forward(df[["pred_min"] + PBASE + add], y, season)
        m = test_mask & ~np.isnan(pts)
        print(f"  {label:<40} points MAE {mean_absolute_error(y[m], pts[m]):.4f}")

    # importance of def_faced in the points model (last season held out)
    cut = seasons[-1]
    tr, te = (season < cut).to_numpy(), (season == cut).to_numpy()
    cols = ["pred_min"] + PBASE + ["def_faced"]
    mdl = gbm(); mdl.fit(df[cols][tr], y[tr])
    r = permutation_importance(mdl, df[cols][te], y[te], scoring="neg_mean_absolute_error",
                               n_repeats=5, random_state=7)
    imp = dict(zip(cols, r.importances_mean))
    print(f"\n  def_faced importance (MAE increase when shuffled): {imp['def_faced']:+.4f}")
    print(f"  (for scale: opp_def {imp['opp_def']:+.4f}, recent_pts10 {imp['recent_pts10']:+.4f})")

    # descriptive: do high-defense games actually see lower scoring? (residual check)
    df["exp"] = df["pred_min"] * df["proj_pts36"] / 36.0
    df["resid"] = df.points - df.exp
    q = df.def_faced.quantile([0.2, 0.8])
    easy = df[df.def_faced <= q.iloc[0]].resid.mean()
    hard = df[df.def_faced >= q.iloc[1]].resid.mean()
    print(f"\n  Scoring vs expectation: vs WEAK D {easy:+.2f} pts, vs ELITE D {hard:+.2f} pts "
          f"(gap {easy - hard:.2f})")


if __name__ == "__main__":
    main()
