"""
Mode-1 simulator v2: efficiency + pace (points per 100 possessions & tempo).

v1 modeled points-per-game directly. v2 decomposes it the right way (Dean Oliver /
KenPom style): each team carries a rolling offensive & defensive efficiency
(points per 100 possessions) and a pace (possessions per game), all leakage-free.
A matchup's possessions come from both teams' tempo; points come from efficiency
x possessions. This separates "how well" from "how fast" and should sharpen the
score prediction (especially totals).

Possessions are computed from game_logs box totals (all 13 seasons):
    poss = FGA + 0.44*FTA - OREB + TOV

Backtested head-to-head vs. v1 and Elo.

Usage: python scripts/53_sim_mode1_v2.py
"""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "data" / "parquet" / "game_logs.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
ELO = ROOT / "data" / "features" / "elo_predictions.parquet"
SIMV1 = ROOT / "data" / "features" / "sim_mode1_predictions.parquet"

EK = 0.14      # efficiency learning rate
PK = 0.12      # pace learning rate
HCA_ORTG = 2.0  # home-court, in offensive-rating (per-100) points
REVERT = 0.25
BURN_IN = "2013-14"


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def build_games():
    lg = pd.read_parquet(LOGS, columns=["GAME_ID", "TEAM_ID", "PTS", "FGA", "FTA",
                                        "OREB", "TOV"])
    lg["POSS"] = lg.FGA + 0.44 * lg.FTA - lg.OREB + lg.TOV
    poss = {(r.GAME_ID, r.TEAM_ID): r.POSS for r in lg.itertuples()}
    g = pd.read_parquet(GAMES).sort_values(["GAME_DATE", "GAME_ID"])
    g["HP"] = [poss.get((gid, t), np.nan) for gid, t in zip(g.GAME_ID, g.HOME_TEAM_ID)]
    g["AP"] = [poss.get((gid, t), np.nan) for gid, t in zip(g.GAME_ID, g.AWAY_TEAM_ID)]
    return g.dropna(subset=["HP", "AP"])


def run(g):
    off = defaultdict(float); dfn = defaultdict(float); pace = defaultdict(float)
    lg_ortg = 106.0; lg_pace = 95.0
    prev = None
    rows = []
    for r in g.itertuples():
        if prev is not None and r.SEASON != prev:
            for d in (off, dfn, pace):
                for t in list(d): d[t] *= (1 - REVERT)
        prev = r.SEASON
        h, a = r.HOME_TEAM_ID, r.AWAY_TEAM_ID
        exp_poss = lg_pace + pace[h] + pace[a]
        h_ortg = lg_ortg + off[h] + dfn[a] + (0 if r.NEUTRAL else HCA_ORTG)
        a_ortg = lg_ortg + off[a] + dfn[h]
        mu_h = h_ortg * exp_poss / 100.0
        mu_a = a_ortg * exp_poss / 100.0
        rows.append((r.GAME_ID, r.SEASON, r.SEASON_TYPE, mu_h, mu_a,
                     r.HOME_PTS, r.AWAY_PTS, r.HOME_WIN))
        # update from residuals, on the per-100 scale
        game_pace = (r.HP + r.AP) / 2.0
        act_h_ortg = r.HOME_PTS / r.HP * 100.0
        act_a_ortg = r.AWAY_PTS / r.AP * 100.0
        eh = act_h_ortg - h_ortg; ea = act_a_ortg - a_ortg
        off[h] += EK * eh / 2; dfn[a] += EK * eh / 2
        off[a] += EK * ea / 2; dfn[h] += EK * ea / 2
        pdev = game_pace - exp_poss
        pace[h] += PK * pdev / 2; pace[a] += PK * pdev / 2
        lg_ortg += 0.01 * ((act_h_ortg + act_a_ortg) / 2 - lg_ortg)
        lg_pace += 0.01 * (game_pace - lg_pace)
    return pd.DataFrame(rows, columns=["GAME_ID", "SEASON", "SEASON_TYPE",
                                       "MU_HOME", "MU_AWAY", "HOME_PTS", "AWAY_PTS", "HOME_WIN"])


def wp_metrics(p, y, label):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    acc = ((p > 0.5).astype(int) == y).mean()
    ll = -(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()
    br = ((p - y) ** 2).mean()
    print(f"  {label:<16} acc {acc:.3f}  logloss {ll:.4f}  brier {br:.4f}")


def main():
    g = build_games()
    pred = run(g)
    ev = pred[pred.SEASON != BURN_IN].copy()
    ev["pmar"] = ev.MU_HOME - ev.MU_AWAY
    ev["ptot"] = ev.MU_HOME + ev.MU_AWAY
    ev["amar"] = ev.HOME_PTS - ev.AWAY_PTS
    ev["atot"] = ev.HOME_PTS + ev.AWAY_PTS
    msd = (ev.amar - ev.pmar).std()
    ev["P_HOME"] = ev.pmar.apply(lambda m: phi(m / msd))

    print(f"Mode-1 v2 (efficiency+pace) — {len(ev):,} games\n")
    print("Win probability:")
    wp_metrics(ev.P_HOME.to_numpy(), ev.HOME_WIN.to_numpy(), "v2 (this)")
    v1 = pd.read_parquet(SIMV1)[["GAME_ID", "P_HOME"]].rename(columns={"P_HOME": "P1"})
    elo = pd.read_parquet(ELO)[["GAME_ID", "P_HOME"]].rename(columns={"P_HOME": "PE"})
    j = ev.merge(v1, on="GAME_ID").merge(elo, on="GAME_ID")
    wp_metrics(j.P1.to_numpy(), j.HOME_WIN.to_numpy(), "v1 (points)")
    wp_metrics(j.PE.to_numpy(), j.HOME_WIN.to_numpy(), "Elo")

    print("\nScore prediction MAE:")
    print(f"  v2  margin {abs(ev.amar - ev.pmar).mean():.2f}   total {abs(ev.atot - ev.ptot).mean():.2f}")
    v1sim = pd.read_parquet(SIMV1)
    v1_mar = abs((v1sim.HOME_PTS - v1sim.AWAY_PTS) - (v1sim.MU_HOME - v1sim.MU_AWAY)).mean()
    v1_tot = abs((v1sim.HOME_PTS + v1sim.AWAY_PTS) - (v1sim.MU_HOME + v1sim.MU_AWAY)).mean()
    print(f"  v1  margin {v1_mar:.2f}   total {v1_tot:.2f}")


if __name__ == "__main__":
    main()
