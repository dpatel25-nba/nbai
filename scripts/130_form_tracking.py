"""
State-space form tracking — is a Kalman filter better than rolling means?

Every in-season form feature in this repo is a flat rolling mean: recent_min3,
recent_min5, recent_min10, recent_pts5, recent_pts10. A flat window is two
assumptions in a trench coat — that the last N games matter equally, and that
game N+1 matters not at all. Neither is true of a player whose role is drifting.

The principled alternative treats a player's true rate as a hidden state:

    theta_t = theta_{t-1} + w_t      w ~ N(0, sigma^2_process)   role drifts
    y_t     = theta_t     + v_t      v ~ N(0, sigma^2_obs)       games are noisy

For a random walk observed with noise, the steady-state Kalman filter collapses
to exponential smoothing with a single gain alpha, set by the variance ratio
q = sigma^2_process / sigma^2_obs:

    alpha = (-q + sqrt(q^2 + 4q)) / 2

So the question is empirical and cheap to settle: does an exponentially-weighted
estimate beat the flat windows at predicting a player's NEXT game, and is the
theoretically-implied alpha the same one that actually wins?

Script 126 is the cautionary tale here — the textbook estimator (split-half
reliability) lost to a direct search on the real objective, because it measured
the wrong variance. So both are computed and compared, and alpha is chosen on
early seasons and scored on later ones.

Usage: python scripts/130_form_tracking.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "data" / "parquet" / "player_games.parquet"
OUT = ROOT / "data" / "parquet" / "form_alpha.parquet"

STATS = {"MIN": "MIN", "points": "points",
         "reboundsTotal": "reboundsTotal", "assists": "assists"}
ALPHAS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.65]
WARMUP = 5          # games needed before a prediction is scored


def build(pg: pd.DataFrame, col: str):
    """-> DataFrame of per-game predictors and the actual next value."""
    rows = []
    for (pid, season), d in pg.groupby(["PLAYER_ID", "SEASON"], sort=False):
        v = d[col].to_numpy(dtype=float)
        if len(v) < WARMUP + 3:
            continue
        ew = {a: v[0] for a in ALPHAS}
        for i in range(1, len(v)):
            for a in ALPHAS:                     # update BEFORE predicting game i
                ew[a] = a * v[i - 1] + (1 - a) * ew[a]
            if i < WARMUP:
                continue
            rec = {"SEASON": season, "y": v[i],
                   "m3": v[max(0, i - 3):i].mean(),
                   "m5": v[max(0, i - 5):i].mean(),
                   "m10": v[max(0, i - 10):i].mean(),
                   "season_td": v[:i].mean()}
            for a in ALPHAS:
                rec[f"ew{a}"] = ew[a]
            rows.append(rec)
    return pd.DataFrame(rows)


def variance_ratio(pg: pd.DataFrame, col: str) -> float:
    """Estimate q = sigma^2_process / sigma^2_obs from the series itself.

    For a random walk plus noise, Var(first difference) = sigma^2_process +
    2*sigma^2_obs and the lag-1 autocovariance of differences is -sigma^2_obs.
    """
    proc, obs = [], []
    for _, d in pg.groupby(["PLAYER_ID", "SEASON"], sort=False):
        v = d[col].to_numpy(dtype=float)
        if len(v) < 15:
            continue
        dv = np.diff(v)
        g0 = float(np.var(dv))
        g1 = float(np.mean((dv[:-1] - dv.mean()) * (dv[1:] - dv.mean())))
        so = max(-g1, 1e-6)
        sp = max(g0 - 2 * so, 1e-6)
        obs.append(so)
        proc.append(sp)
    if not proc:
        return np.nan
    return float(np.median(proc) / np.median(obs))


def kalman_alpha(q: float) -> float:
    return float((-q + np.sqrt(q * q + 4 * q)) / 2) if q > 0 else np.nan


def main() -> None:
    pg = pd.read_parquet(PG, columns=["GAME_ID", "GAME_DATE", "SEASON", "SEASON_TYPE",
                                      "PLAYER_ID"] + list(STATS))
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)]
    pg = pg.sort_values(["PLAYER_ID", "SEASON", "GAME_DATE"])

    seasons = sorted(pg.SEASON.unique())
    cut = seasons[len(seasons) * 2 // 3]
    print(f"Form tracking — alpha chosen on seasons < {cut}, scored on >= {cut}\n")

    out = []
    for col in STATS:
        d = build(pg, col)
        if not len(d):
            continue
        tr, te = d[d.SEASON < cut], d[d.SEASON >= cut]
        q = variance_ratio(pg, col)
        a_theory = kalman_alpha(q)

        # pick alpha on the training seasons only
        best_a, best_e = None, None
        for a in ALPHAS:
            e = float(np.abs(tr[f"ew{a}"] - tr.y).mean())
            if best_e is None or e < best_e:
                best_a, best_e = a, e

        mae = {name: float(np.abs(te[name] - te.y).mean())
               for name in ["m3", "m5", "m10", "season_td"]}
        mae[f"EWMA a={best_a}"] = float(np.abs(te[f"ew{best_a}"] - te.y).mean())
        a_th = min(ALPHAS, key=lambda a: abs(a - a_theory)) if np.isfinite(a_theory) else None
        if a_th is not None:
            mae[f"EWMA a={a_th} (Kalman)"] = float(np.abs(te[f"ew{a_th}"] - te.y).mean())

        best_flat = min(mae[k] for k in ["m3", "m5", "m10", "season_td"])
        ew_best = mae[f"EWMA a={best_a}"]
        print(f"=== {col} ===   q={q:.3f}  Kalman alpha={a_theory:.2f}  "
              f"searched alpha={best_a}")
        for k, v in sorted(mae.items(), key=lambda kv: kv[1]):
            tag = "  <-- best" if v == min(mae.values()) else ""
            print(f"   {k:<24}{v:>9.4f}{tag}")
        print(f"   EWMA vs best flat window: {(ew_best / best_flat - 1) * 100:+.2f}%\n")
        out.append({"stat": col, "q": q, "alpha_kalman": a_theory,
                    "alpha_searched": best_a, "mae_ewma": ew_best,
                    "mae_best_flat": best_flat,
                    "improvement_pct": (ew_best / best_flat - 1) * 100})

    df = pd.DataFrame(out)
    df.to_parquet(OUT, index=False)
    win = int((df.improvement_pct < 0).sum())
    print(f"EWMA beats the best flat window on {win}/{len(df)} statistics.")
    print(f"Saved -> {OUT}")


if __name__ == "__main__":
    main()
