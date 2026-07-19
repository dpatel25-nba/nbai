"""
Player projection engine (v1, age-independent) + leakage-free backtest.

Marcel-style projection: a player's next-season value is a recency-weighted blend
of recent seasons, each weighted by minutes (reliability), then regressed toward
the league mean by a constant K of "phantom" minutes. Only prior seasons are used,
so every projection is out-of-sample.

    proj = (Σ w_i·min_i·value_i  +  K·prior_mean) / (Σ w_i·min_i  +  K)

Backtest: for each season T, project returning players from seasons < T and compare
to what they actually did in T. Reported against naive baselines to prove lift.
Aging curves, rookie priors, and minutes projection layer on later.

Usage: python scripts/23_project_players.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"

RECENCY = {1: 5.0, 2: 4.0, 3: 3.0}   # weight for T-1, T-2, T-3
K = 1000.0                            # regression strength (phantom minutes)
MIN_EVAL = 500                        # only score players with a real role in season T


def season_order(seasons) -> dict:
    return {s: i for i, s in enumerate(sorted(seasons))}


def project(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    order = season_order(df["SEASON"].unique())
    inv = {i: s for s, i in order.items()}
    val = {(r.PLAYER_ID, r.SEASON): getattr(r, metric) for r in df.itertuples()}
    mn = {(r.PLAYER_ID, r.SEASON): r.MIN for r in df.itertuples()}

    out = []
    for r in df.itertuples():
        ti = order[r.SEASON]
        if ti == 0:
            continue  # no prior seasons exist
        # leakage-free regression target: league mean over seasons strictly before T
        prior = df[df["SEASON"].map(order) < ti]
        prior_mean = np.average(prior[metric].dropna(),
                                weights=prior.loc[prior[metric].notna(), "MIN"])
        num = den = 0.0
        used = 0
        for lag, w in RECENCY.items():
            s = inv.get(ti - lag)
            if s is None:
                continue
            v, m = val.get((r.PLAYER_ID, s)), mn.get((r.PLAYER_ID, s))
            if v is None or m is None or np.isnan(v):
                continue
            num += w * m * v
            den += w * m
            used += 1
        if used == 0:
            continue  # rookie / no usable history -> needs the rookie-prior layer
        proj = (num + K * prior_mean) / (den + K)
        naive1 = val.get((r.PLAYER_ID, inv[ti - 1]))  # last season only
        out.append({"PLAYER_ID": r.PLAYER_ID, "PLAYER": r.PLAYER, "SEASON": r.SEASON,
                    "MIN_T": r.MIN, "actual": getattr(r, metric), "proj": proj,
                    "naive1": naive1, "prior_mean": prior_mean})
    return pd.DataFrame(out)


def score(p: pd.DataFrame, col: str, ref: str) -> dict:
    d = p.dropna(subset=[ref, "actual"])
    e = d[ref] - d["actual"]
    r = np.corrcoef(d[ref], d["actual"])[0, 1]
    return {"MAE": abs(e).mean(), "r": r, "n": len(d)}


def run(df: pd.DataFrame, metric: str) -> None:
    p = project(df, metric)
    p = p[p["MIN_T"] >= MIN_EVAL]
    print(f"\n=== Projecting {metric}  (players with ≥{MIN_EVAL} min in target season) ===")
    print(f"evaluated player-seasons: {len(p):,}")
    for name, ref in [("Projection (Marcel)", "proj"),
                      ("Naive: last season", "naive1"),
                      ("Naive: league mean", "prior_mean")]:
        s = score(p, metric, ref)
        print(f"  {name:<22} MAE {s['MAE']:.4f}   corr {s['r']:.3f}   n {s['n']:,}")
    # lift vs last-season baseline
    proj_mae = score(p, metric, "proj")["MAE"]
    base_mae = score(p, metric, "naive1")["MAE"]
    print(f"  -> {(1 - proj_mae / base_mae) * 100:.1f}% lower error than 'last season'")


def main() -> None:
    df = pd.read_parquet(PS)
    print(f"player_seasons: {len(df):,} rows, {df.SEASON.nunique()} seasons")
    for metric in ["PIE", "PTS_36", "REB_36", "AST_36"]:
        run(df, metric)


if __name__ == "__main__":
    main()
