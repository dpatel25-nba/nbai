"""
Player stat-line layer for the Mode-1 simulator (MVP) + backtest.

Turns a simulated game into per-player stat lines:
  1. project each player's per-36 rates (PTS/REB/AST) leakage-free (Marcel-style,
     prior seasons only),
  2. scale by minutes -> raw expected line,
  3. reconcile each team's points so they sum to the Mode-1 simulated team total
     (the opponent-adjusted, well-calibrated game prediction flows down to players).

Validated on real box scores: MAE + interval coverage vs. a naive baseline.

Scope (honest): this holds rotation/minutes fixed to what actually happened
(the "fixed-lineup" stage in the build plan) so we isolate the *allocation*
quality — projecting minutes is the rotation model, a later phase.

Usage: python scripts/51_sim_players.py
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
SIM = ROOT / "data" / "features" / "sim_mode1_predictions.parquet"

RECENCY = {1: 5.0, 2: 4.0, 3: 3.0}
K = 1000.0


def project_rate(df: pd.DataFrame, metric: str) -> dict:
    """(player, season) -> leakage-free projected per-36 rate (prior seasons only)."""
    order = {s: i for i, s in enumerate(sorted(df.SEASON.unique()))}
    inv = {i: s for s, i in order.items()}
    val = {(r.PLAYER_ID, r.SEASON): getattr(r, metric) for r in df.itertuples()}
    mn = {(r.PLAYER_ID, r.SEASON): r.MIN for r in df.itertuples()}
    out = {}
    for r in df.itertuples():
        ti = order[r.SEASON]
        if ti == 0:
            continue
        prior = df[df.SEASON.map(order) < ti]
        pm = np.average(prior[metric].dropna(),
                        weights=prior.loc[prior[metric].notna(), "MIN"])
        num = den = 0.0
        for lag, w in RECENCY.items():
            s = inv.get(ti - lag)
            v, m = val.get((r.PLAYER_ID, s)), mn.get((r.PLAYER_ID, s))
            if v is not None and m is not None and not np.isnan(v):
                num += w * m * v; den += w * m
        if den:
            out[(r.PLAYER_ID, r.SEASON)] = (num + K * pm) / (den + K)
    return out


def mae(pred, act):
    return np.abs(np.asarray(pred) - np.asarray(act)).mean()


def main() -> None:
    ps = pd.read_parquet(PS)
    proj = {m: project_rate(ps, f"{m}_36") for m in ("PTS", "REB", "AST")}
    last = {m: {(r.PLAYER_ID, r.SEASON): getattr(r, f"{m}_36") for r in ps.itertuples()}
            for m in ("PTS", "REB", "AST")}
    order = {s: i for i, s in enumerate(sorted(ps.SEASON.unique()))}
    inv = {i: s for s, i in order.items()}

    pg = pd.read_parquet(PG, columns=["GAME_ID", "SEASON", "SEASON_TYPE", "TEAM_ID",
                                       "PLAYER_ID", "MIN", "points", "reboundsTotal", "assists"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)].copy()

    # Mode-1 predicted points per (game, team)
    sim = pd.read_parquet(SIM)[["GAME_ID", "MU_HOME", "MU_AWAY"]]
    g = pd.read_parquet(GAMES)[["GAME_ID", "HOME_TEAM_ID", "AWAY_TEAM_ID"]]
    sim = sim.merge(g, on="GAME_ID")
    team_mu = {}
    for r in sim.itertuples():
        team_mu[(r.GAME_ID, r.HOME_TEAM_ID)] = r.MU_HOME
        team_mu[(r.GAME_ID, r.AWAY_TEAM_ID)] = r.MU_AWAY

    # raw expected lines from projected per-36 rates
    for m, col in [("PTS", "pts"), ("REB", "reb"), ("AST", "ast")]:
        pg[f"proj_{col}"] = [proj[m].get((p, s), np.nan) for p, s in zip(pg.PLAYER_ID, pg.SEASON)]
        pg[f"base_{col}"] = [last[m].get((p, inv[order[s] - 1]), np.nan) if order[s] > 0 else np.nan
                             for p, s in zip(pg.PLAYER_ID, pg.SEASON)]
    pg = pg.dropna(subset=["proj_pts"])
    for col in ("pts", "reb", "ast"):
        pg[f"exp_{col}"] = pg[f"proj_{col}"] * pg.MIN / 36.0
        pg[f"expbase_{col}"] = pg[f"base_{col}"] * pg.MIN / 36.0

    # reconcile team points to the Mode-1 simulated team total
    team_raw = pg.groupby(["GAME_ID", "TEAM_ID"])["exp_pts"].transform("sum")
    mu = np.array([team_mu.get((gid, tid), np.nan) for gid, tid in zip(pg.GAME_ID, pg.TEAM_ID)])
    pg["exp_pts_rec"] = pg["exp_pts"] * mu / team_raw
    pg = pg.dropna(subset=["exp_pts_rec"])

    print(f"Player-games evaluated: {len(pg):,}\n")
    print("Points-per-game projection (MAE, lower is better):")
    print(f"  {'naive (last season)':<26} {mae(pg.expbase_pts.fillna(pg.exp_pts), pg.points):.3f}")
    print(f"  {'projected rate x min':<26} {mae(pg.exp_pts, pg.points):.3f}")
    print(f"  {'+ reconciled to sim':<26} {mae(pg.exp_pts_rec, pg.points):.3f}  <- full model")
    for label, col, actual in [("Rebounds", "exp_reb", "reboundsTotal"),
                               ("Assists", "exp_ast", "assists")]:
        print(f"\n{label} projection MAE: {mae(pg[col], pg[actual]):.3f}")

    # distributional check: over-dispersed count intervals, coverage of 80% band
    print("\nInterval coverage (target 80%):")
    for col, actual in [("exp_pts_rec", "points"), ("exp_reb", "reboundsTotal"), ("exp_ast", "assists")]:
        e = pg[col].to_numpy(); y = pg[actual].to_numpy()
        disp = np.mean((y - e) ** 2 / np.clip(e, 0.5, None))     # over-dispersion factor
        sd = np.sqrt(disp * np.clip(e, 0.5, None))
        cover = np.mean(np.abs(y - e) <= 1.2816 * sd)
        print(f"  {actual:<16} dispersion {disp:.2f}  ->  {cover*100:.1f}% within 80% interval")

    # face check
    star = pg[(pg.PLAYER_ID == 201939)]  # Stephen Curry
    if len(star):
        print(f"\nFace check — Stephen Curry, projected vs actual per game:")
        s = star.assign(pm=star.exp_pts_rec).groupby("SEASON").agg(
            GP=("GAME_ID", "size"), proj_pts=("exp_pts_rec", "mean"), act_pts=("points", "mean"),
            proj_ast=("exp_ast", "mean"), act_ast=("assists", "mean"))
        print(s.round(1).to_string())


if __name__ == "__main__":
    main()
