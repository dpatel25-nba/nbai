"""
Mode-1 distributional game simulator (MVP) + leakage-free backtest.

An opponent-adjusted scoring model: each team carries a rolling offensive and
defensive rating (points above league average), updated after every game and
regressed between seasons — all from prior games only, so predictions are
out-of-sample by construction. For a matchup it predicts each team's expected
points, then treats the game as distributions:

    margin ~ Normal(mu_home - mu_away, MARGIN_SD)
    total  ~ Normal(mu_home + mu_away, TOTAL_SD)

giving a win probability AND full margin/total distributions (the "simulator"
output). Backtested head-to-head vs. the Elo baseline on the same games.

Usage: python scripts/50_sim_mode1.py
"""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GAMES = ROOT / "data" / "parquet" / "games.parquet"
ELO = ROOT / "data" / "features" / "elo_predictions.parquet"
OUT = ROOT / "data" / "features" / "sim_mode1_predictions.parquet"

K = 0.15          # rating learning rate
LK = 0.01         # league-average learning rate
HCA = 2.8         # home-court points advantage
REVERT = 0.25     # between-season regression toward mean
BURN_IN = "2013-14"


def phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def run_ratings(games: pd.DataFrame) -> pd.DataFrame:
    off = defaultdict(float)   # team offensive rating (pts above avg scored)
    dfn = defaultdict(float)   # team defensive rating (pts above avg allowed)
    league = games["HOME_PTS"].head(200).mean()  # seed league avg pts/team
    prev_season = None
    rows = []
    for g in games.itertuples():
        if prev_season is not None and g.SEASON != prev_season:
            for t in list(off): off[t] *= (1 - REVERT)
            for t in list(dfn): dfn[t] *= (1 - REVERT)
        prev_season = g.SEASON
        h, a = g.HOME_TEAM_ID, g.AWAY_TEAM_ID
        hca = 0.0 if g.NEUTRAL else HCA
        mu_h = league + off[h] + dfn[a] + hca
        mu_a = league + off[a] + dfn[h]
        rows.append((g.GAME_ID, g.SEASON, g.SEASON_TYPE, mu_h, mu_a,
                     g.HOME_PTS, g.AWAY_PTS, g.HOME_WIN))
        # update from residuals (split between offense and opponent defense)
        eh, ea = g.HOME_PTS - mu_h, g.AWAY_PTS - mu_a
        off[h] += K * eh / 2; dfn[a] += K * eh / 2
        off[a] += K * ea / 2; dfn[h] += K * ea / 2
        league += LK * ((g.HOME_PTS + g.AWAY_PTS) / 2 - league)
    return pd.DataFrame(rows, columns=["GAME_ID", "SEASON", "SEASON_TYPE",
                                       "MU_HOME", "MU_AWAY", "HOME_PTS", "AWAY_PTS", "HOME_WIN"])


def metrics(p, y, label):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    acc = ((p > 0.5).astype(int) == y).mean()
    ll = -(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()
    brier = ((p - y) ** 2).mean()
    print(f"  {label:<20} acc {acc:.3f}   logloss {ll:.4f}   brier {brier:.4f}")
    return acc, ll, brier


def main() -> None:
    games = pd.read_parquet(GAMES).sort_values(["GAME_DATE", "GAME_ID"])
    pred = run_ratings(games)

    ev = pred[pred.SEASON != BURN_IN].copy()
    ev["pred_margin"] = ev.MU_HOME - ev.MU_AWAY
    ev["pred_total"] = ev.MU_HOME + ev.MU_AWAY
    ev["act_margin"] = ev.HOME_PTS - ev.AWAY_PTS
    ev["act_total"] = ev.HOME_PTS + ev.AWAY_PTS

    # calibration constants = residual spread (the simulator's uncertainty)
    MARGIN_SD = (ev.act_margin - ev.pred_margin).std()
    TOTAL_SD = (ev.act_total - ev.pred_total).std()
    ev["P_HOME"] = ev["pred_margin"].apply(lambda m: phi(m / MARGIN_SD))
    ev.to_parquet(OUT, index=False)

    print(f"Mode-1 simulator — evaluated on {len(ev):,} games (burn-in {BURN_IN} excluded)")
    print(f"Uncertainty (fit): margin SD {MARGIN_SD:.1f} pts | total SD {TOTAL_SD:.1f} pts\n")

    print("Win-probability (head-to-head vs Elo):")
    metrics(ev.P_HOME.to_numpy(), ev.HOME_WIN.to_numpy(), "Mode-1 sim")
    elo = pd.read_parquet(ELO)[["GAME_ID", "P_HOME"]].rename(columns={"P_HOME": "P_ELO"})
    j = ev.merge(elo, on="GAME_ID")
    metrics(j.P_ELO.to_numpy(), j.HOME_WIN.to_numpy(), "Elo baseline")

    print(f"\nScore prediction (unique to the simulator):")
    print(f"  margin MAE {abs(ev.act_margin - ev.pred_margin).mean():.2f} pts")
    print(f"  total  MAE {abs(ev.act_total - ev.pred_total).mean():.2f} pts")

    print("\nWin-prob calibration (predicted vs actual):")
    ev["bin"] = pd.cut(ev.P_HOME, [i/10 for i in range(11)], include_lowest=True)
    tb = ev.groupby("bin", observed=True).agg(n=("HOME_WIN", "size"),
        pred=("P_HOME", "mean"), actual=("HOME_WIN", "mean"))
    for idx, r in tb.iterrows():
        print(f"  {str(idx):<14} n={int(r.n):>5}  pred {r.pred:.3f}  actual {r.actual:.3f}")

    # --- Monte Carlo demo: full distribution for one matchup ---
    print("\nDistribution output demo (Monte Carlo, one matchup):")
    g = ev.iloc[-500]  # a representative game
    n = 20000
    rng = np.random.default_rng(0)
    margins = rng.normal(g.pred_margin, MARGIN_SD, n)
    totals = rng.normal(g.pred_total, TOTAL_SD, n)
    home = (totals + margins) / 2; away = (totals - margins) / 2
    print(f"  proj: home {g.MU_HOME:.1f} - {g.MU_AWAY:.1f} away  ({g.SEASON})")
    print(f"  home win prob {(margins > 0).mean()*100:.1f}%  | "
          f"cover -5.5: {(margins > 5.5).mean()*100:.1f}%  | over 225.5: {(totals > 225.5).mean()*100:.1f}%")
    print(f"  home score p10/p50/p90: {np.percentile(home,10):.0f}/"
          f"{np.percentile(home,50):.0f}/{np.percentile(home,90):.0f}")
    print(f"\nSaved predictions -> {OUT}")


if __name__ == "__main__":
    main()
