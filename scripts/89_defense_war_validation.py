"""
Does matchup-based defender skill improve the DEFENSIVE side of WAR?

Box DBPM is the known-weak part of our WAR (box scores barely see defense). The
matchup metric is a candidate fix. The honest test of a defensive metric is
PORTABILITY: take players' PRIOR-season defensive ratings, weight by their
minutes on THIS season's roster, and predict the team's ACTUAL defensive rating.
Roster churn makes this a real out-of-sample test — a metric that only refits the
same season proves nothing.

  team_pred_T = Σ(min_i · rating_i^{T-1}) / Σ min_i   over the team's players

We compare, via leave-one-season-out CV:
    box DBPM alone   vs   box DBPM + matchup skill
predicting team defensive rating. If matchup skill lowers CV error, it carries
orthogonal, portable defensive signal that box stats lack.

Usage: python scripts/89_defense_war_validation.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
WAR = ROOT / "data" / "parquet" / "player_seasons_war.parquet"
DQ = ROOT / "data" / "parquet" / "defender_quality_v2.parquet"
TG = ROOT / "data" / "parquet" / "team_games.parquet"

ORDER = ["2013-14", "2014-15", "2015-16", "2016-17", "2017-18", "2018-19",
         "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
PREV = {s: ORDER[i - 1] for i, s in enumerate(ORDER) if i}


def team_defensive_rating():
    tg = pd.read_parquet(TG)
    tg = tg[tg.SEASON_TYPE == "Regular Season"]
    out = {}
    for (tri, s), g in tg.groupby(["TEAM_TRICODE", "SEASON"]):
        out[(tri, s)] = g["defensiveRating"].mean()
    return out


def weighted_prior(players, rating_prev):
    """minutes-weighted mean of prior-season ratings over players that have one."""
    num = den = 0.0
    for pid, mins in players:
        r = rating_prev.get(pid)
        if r is not None:
            num += mins * r; den += mins
    return num / den if den > 0 else np.nan


def loso_cv(X, y, seasons):
    """leave-one-season-out linear CV; returns pooled predictions."""
    pred = np.full(len(y), np.nan)
    for s in np.unique(seasons):
        tr, te = seasons != s, seasons == s
        A = np.column_stack([np.ones(tr.sum()), X[tr]])
        b, *_ = np.linalg.lstsq(A, y[tr], rcond=None)
        pred[te] = np.column_stack([np.ones(te.sum()), X[te]]) @ b
    return pred


def main() -> None:
    war = pd.read_parquet(WAR)
    dq = pd.read_parquet(DQ)
    drtg = team_defensive_rating()

    # prior-season rating lookups keyed by player id
    dbpm_prev, skill_prev = {}, {}
    for r in war.itertuples():
        dbpm_prev[(r.PLAYER_ID, r.SEASON)] = r.DBPM
    for r in dq.itertuples():
        skill_prev[(r.PLAYER_ID, r.SEASON)] = r.skill

    # build team-season rows (only seasons where matchup skill has a prior year)
    rows = []
    for (tri, s), g in war.groupby(["TEAM", "SEASON"]):
        ps = PREV.get(s)
        if ps is None or s not in {"2018-19", "2019-20", "2020-21", "2021-22", "2022-23"}:
            continue
        if (tri, s) not in drtg:
            continue
        players = [(r.PLAYER_ID, r.MIN) for r in g.itertuples() if r.MIN and r.MIN > 0]
        box = weighted_prior(players, {k[0]: v for k, v in dbpm_prev.items() if k[1] == ps})
        mu = weighted_prior(players, {k[0]: v for k, v in skill_prev.items() if k[1] == ps})
        if np.isnan(box) or np.isnan(mu):
            continue
        rows.append({"team": tri, "season": s, "drtg": drtg[(tri, s)],
                     "box_prior": box, "mu_prior": mu})

    d = pd.DataFrame(rows)
    print(f"Team-seasons in validation: {len(d)}  ({d.season.min()}…{d.season.max()})")

    # Control for season: league DRtg drifts every year, so demean everything within
    # season. This isolates the signal we care about — among teams IN THE SAME YEAR,
    # do better prior defender ratings mean a better defense?
    for c in ["drtg", "box_prior", "mu_prior"]:
        d[c + "_dm"] = d[c] - d.groupby("season")[c].transform("mean")
    y = d.drtg_dm.to_numpy()

    print("\nWithin-season correlation with team DRtg (negative = better D predicted):")
    print(f"  box DBPM (prior)      r = {np.corrcoef(d.box_prior_dm, y)[0,1]:+.3f}")
    print(f"  matchup skill (prior) r = {np.corrcoef(d.mu_prior_dm, y)[0,1]:+.3f}")

    def r2(cols):
        A = np.column_stack([np.ones(len(d))] + [d[c].to_numpy() for c in cols])
        b, *_ = np.linalg.lstsq(A, y, rcond=None)
        pred = A @ b
        return 1 - np.sum((y - pred) ** 2) / np.sum((y - y.mean()) ** 2), b[-1]

    print("\nExplained variance in (season-adjusted) team DRtg:")
    r2_box, _ = r2(["box_prior_dm"])
    r2_mu, _ = r2(["mu_prior_dm"])
    r2_both, coef_mu = r2(["box_prior_dm", "mu_prior_dm"])
    print(f"  box DBPM only              R² = {r2_box:.3f}")
    print(f"  matchup skill only         R² = {r2_mu:.3f}")
    print(f"  box DBPM + matchup skill   R² = {r2_both:.3f}")
    print(f"\n  Incremental R² from matchup skill: {r2_both - r2_box:+.3f}  "
          f"(matchup coef {coef_mu:+.3f})")

    # leave-one-season-out CV on the season-adjusted target (honest generalization)
    seasons = d.season.to_numpy()
    print("\nLeave-one-season-out CV (season-adjusted DRtg):")
    for name, cols in [("box DBPM only", ["box_prior_dm"]),
                       ("box DBPM + matchup skill", ["box_prior_dm", "mu_prior_dm"])]:
        p = loso_cv(d[cols].to_numpy(), y, seasons)
        print(f"  {name:<26} CV RMSE {np.sqrt(np.mean((y - p) ** 2)):.3f}  "
              f"corr {np.corrcoef(p, y)[0,1]:+.3f}")


if __name__ == "__main__":
    main()
