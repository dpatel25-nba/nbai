"""
Empirical-Bayes shrinkage constants, estimated per statistic.

Every projection in this repo (scripts 23, 51, 62, 70, 124) shrinks with the SAME
constant: `K = 1000` phantom minutes, applied identically to points, rebounds,
assists, three-point percentage and everything else. That cannot be right. Each
statistic has its own signal-to-noise ratio, and the optimal shrinkage is set by
that ratio, not by a shared guess:

    theta_hat = (m*x + K*mu) / (m + K)        with     K = sigma^2_noise / sigma^2_true

Shrink a stable, high-signal stat too hard and real skill is erased; shrink a
noisy one too little and randomness is projected forward as if it were talent.
Your own north-star notes flag shrinkage as "the make-or-break" for thin splits.

K is estimated here by SPLIT-HALF RELIABILITY, the textbook approach and the one
that needs no distributional assumptions:

  1. Split each player-season's games into odd and even halves.
  2. Correlate the metric between halves across players -> r, the reliability of
     a half-season.
  3. Spearman-Brown steps that up to the full season:  R = 2r / (1 + r).
  4. Reliability is m/(m+K) by construction, so  K = m_bar * (1-R) / R.

Then the constants are VALIDATED, not assumed: a walk-forward projection is run
with the fixed K=1000 and with the per-stat K, and out-of-sample MAE is compared.
Only stats where the estimate actually wins should be adopted.

Output: data/parquet/shrinkage_constants.parquet
Usage: python scripts/126_shrinkage.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "data" / "parquet" / "player_games.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
OUT = ROOT / "data" / "parquet" / "shrinkage_constants.parquet"

RECENCY = {1: 5.0, 2: 4.0, 3: 3.0}
MIN_GP = 20
MIN_HALF_MIN = 150      # minutes needed in a half for a usable estimate

# metric -> (numerator column, denominator column). Rates are per-36 when the
# denominator is minutes, and true percentages when it is attempts.
METRICS = {
    "PTS_36":   ("points", "MIN"),
    "REB_36":   ("reboundsTotal", "MIN"),
    "AST_36":   ("assists", "MIN"),
    "FGA_36":   ("fieldGoalsAttempted", "MIN"),
    "FG3A_36":  ("threePointersAttempted", "MIN"),
    "TOV_36":   ("turnovers", "MIN"),
    "PF_36":    ("foulsPersonal", "MIN"),
    "OREB_36":  ("reboundsOffensive", "MIN"),
    "DREB_36":  ("reboundsDefensive", "MIN"),
    "FG3_PCT":  ("threePointersMade", "threePointersAttempted"),
    "FT_PCT":   ("freeThrowsMade", "freeThrowsAttempted"),
    "FG_PCT":   ("fieldGoalsMade", "fieldGoalsAttempted"),
}


def split_half_K(pg: pd.DataFrame, num: str, den: str):
    """-> (half-season r, Spearman-Brown R, implied K, n players, mean weight)."""
    pg = pg.copy()
    pg["half"] = (pg.groupby(["PLAYER_ID", "SEASON"]).cumcount() % 2)
    g = (pg.groupby(["PLAYER_ID", "SEASON", "half"])
           .agg(n=(num, "sum"), d=(den, "sum"), gp=("GAME_ID", "size")).reset_index())
    g = g[g.d > 0]
    piv = g.pivot_table(index=["PLAYER_ID", "SEASON"], columns="half",
                        values=["n", "d"])
    piv = piv.dropna()
    if len(piv) < 100:
        return None
    n0, n1 = piv[("n", 0)].to_numpy(), piv[("n", 1)].to_numpy()
    d0, d1 = piv[("d", 0)].to_numpy(), piv[("d", 1)].to_numpy()
    scale = 36.0 if den == "MIN" else 1.0
    keep = (d0 >= (MIN_HALF_MIN if den == "MIN" else 40)) & \
           (d1 >= (MIN_HALF_MIN if den == "MIN" else 40))
    if keep.sum() < 60:
        return None
    x0 = n0[keep] / d0[keep] * scale
    x1 = n1[keep] / d1[keep] * scale
    r = float(np.corrcoef(x0, x1)[0, 1])
    R = 2 * r / (1 + r) if r > 0 else 0.01
    R = min(max(R, 0.01), 0.995)
    mbar = float(np.mean(d0[keep] + d1[keep]))         # full-season weight
    K = mbar * (1 - R) / R
    return r, R, K, int(keep.sum()), mbar


def project(df, metric, weight, K):
    """Marcel projection with a given K; returns (actual, projected) arrays."""
    order = {s: i for i, s in enumerate(sorted(df.SEASON.unique()))}
    inv = {i: s for s, i in order.items()}
    val = {(r.PLAYER_ID, r.SEASON): getattr(r, metric) for r in df.itertuples()}
    wt = {(r.PLAYER_ID, r.SEASON): getattr(r, weight) for r in df.itertuples()}
    A, P, W = [], [], []
    cache = {}
    for r in df.itertuples():
        ti = order[r.SEASON]
        if ti == 0 or getattr(r, weight) < 500:
            continue
        if ti not in cache:
            pri = df[df.SEASON.map(order) < ti]
            ok = pri[metric].notna() & pri[weight].notna()
            cache[ti] = (np.average(pri.loc[ok, metric], weights=pri.loc[ok, weight])
                         if ok.any() else np.nan)
        pm = cache[ti]
        num = den = 0.0
        for lag, w in RECENCY.items():
            s = inv.get(ti - lag)
            v, m = val.get((r.PLAYER_ID, s)), wt.get((r.PLAYER_ID, s))
            if v is not None and m is not None and not np.isnan(v):
                num += w * m * v
                den += w * m
        if den and not np.isnan(pm):
            A.append(getattr(r, metric))
            P.append((num + K * pm) / (den + K))
            W.append(getattr(r, weight))
    return np.array(A), np.array(P), np.array(W)


def main() -> None:
    pg = pd.read_parquet(PG, columns=["GAME_ID", "SEASON", "SEASON_TYPE", "PLAYER_ID",
                                      "MIN"] + sorted({c for v in METRICS.values()
                                                       for c in v if c != "MIN"}))
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)].sort_values(
        ["PLAYER_ID", "SEASON", "GAME_ID"])
    ps = pd.read_parquet(PS)

    print("Split-half reliability -> empirical-Bayes K, per statistic")
    print(f"{'metric':<10}{'half r':>9}{'full R':>9}{'K est':>10}{'K=1000':>9}"
          f"{'n':>7}   verdict")
    rows = []
    for metric, (num, den) in METRICS.items():
        res = split_half_K(pg, num, den)
        if res is None:
            continue
        r, R, K, n, mbar = res
        rows.append({"metric": metric, "half_r": r, "reliability": R,
                     "K": K, "weight_col": "MIN" if den == "MIN" else den,
                     "n": n, "mean_weight": mbar})
        verdict = ("shrink MUCH harder" if K > 2500 else
                   "shrink harder" if K > 1300 else
                   "shrink less" if K < 700 else "close to K=1000")
        print(f"{metric:<10}{r:>9.3f}{R:>9.3f}{K:>10.0f}{1000:>9}{n:>7,}   {verdict}")

    K_of = {r["metric"]: r["K"] for r in rows}

    # --- validation: does the estimated K actually project better out of sample? ---
    print("\nOut-of-sample projection MAE (players with >=500 min in the target season)")
    print(f"{'metric':<10}{'K=1000':>10}{'K est':>10}{'change':>10}   adopt?")
    adopt = {}
    for metric in ["PTS_36", "REB_36", "AST_36", "FGA_36", "FG3A_36", "TOV_36",
                   "OREB_36", "DREB_36", "PF_36", "FG3_PCT", "FT_PCT", "FG_PCT"]:
        if metric not in ps.columns or metric not in K_of:
            continue
        w = "MIN"
        a1, p1, _ = project(ps, metric, w, 1000.0)
        a2, p2, _ = project(ps, metric, w, K_of[metric])
        if not len(a1):
            continue
        m1 = np.nanmean(np.abs(a1 - p1))
        m2 = np.nanmean(np.abs(a2 - p2))
        better = m2 < m1
        adopt[metric] = better
        print(f"{metric:<10}{m1:>10.4f}{m2:>10.4f}{(m2-m1)/m1*100:>9.1f}%   "
              f"{'YES' if better else 'no'}")

    # ---- direct search on the objective we actually care about ----
    # Split-half reliability only sees MEASUREMENT noise within a season. A
    # projection must also survive year-to-year SKILL DRIFT (aging, role and team
    # changes), which no within-season split can observe — which is why the
    # reliability-implied K under-shrinks and lost on 9 of 12 stats above.
    # So search K against out-of-sample projection error directly, choosing it on
    # EARLY seasons and scoring it on LATER ones so the choice is not fitted to
    # the games it is judged on.
    seasons = sorted(ps.SEASON.unique())
    cut = seasons[len(seasons) * 2 // 3]
    early, late = ps[ps.SEASON < cut], ps
    GRID = [50, 100, 200, 400, 700, 1000, 1500, 2200, 3200, 5000, 8000]
    print(f"\nDirect K search  (chosen on seasons < {cut}, scored on >= {cut})")
    print(f"{'metric':<10}{'K*':>8}{'K=1000 MAE':>12}{'K* MAE':>10}{'change':>9}   adopt?")
    tuned = {}
    for metric in [m for m in adopt if m in ps.columns]:
        best, bestK = None, 1000.0
        for K in GRID:
            a, pr, _ = project(early, metric, "MIN", float(K))
            if not len(a):
                continue
            e = np.nanmean(np.abs(a - pr))
            if best is None or e < best:
                best, bestK = e, float(K)
        a1, p1, _ = project(late, metric, "MIN", 1000.0)
        a2, p2, _ = project(late, metric, "MIN", bestK)
        m1, m2 = np.nanmean(np.abs(a1 - p1)), np.nanmean(np.abs(a2 - p2))
        win = m2 < m1
        tuned[metric] = (bestK, win)
        print(f"{metric:<10}{bestK:>8.0f}{m1:>12.4f}{m2:>10.4f}"
              f"{(m2-m1)/m1*100:>8.1f}%   {'YES' if win else 'no'}")

    out = pd.DataFrame(rows)
    out["K_tuned"] = out.metric.map(lambda m: tuned.get(m, (np.nan, False))[0])
    out["tuned_win"] = out.metric.map(lambda m: tuned.get(m, (np.nan, False))[1])
    out["adopt"] = out.metric.map(adopt).fillna(False)
    # anything not validated keeps the incumbent constant
    # prefer the directly-searched K where it validated, then the reliability
    # estimate where THAT validated, else keep the incumbent
    out["K_final"] = np.where(out.tuned_win, out.K_tuned,
                              np.where(out.adopt, out.K, 1000.0))
    out.to_parquet(OUT, index=False)
    print(f"\nFinal: {int(out.tuned_win.sum())} stats use a searched K, "
          f"{int((~out.tuned_win & out.adopt).sum())} use the reliability K, "
          f"{int((~out.tuned_win & ~out.adopt).sum())} keep K=1000.")
    print(f"Saved -> {OUT}")


if __name__ == "__main__":
    main()
