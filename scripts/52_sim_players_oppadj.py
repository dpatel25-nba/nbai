"""
Player stat-line simulator, v2: opponent + home/away adjustment.

Fixes the limitation the v1 backtest exposed (projected points barely beat naive,
because nothing accounted for the opponent). Now each player's projected scoring
is scaled by:
  - the opponent's rolling DEFENSIVE rating (leakage-free, prior games only), and
  - a small home/away factor.

Measured head-to-head vs. the unadjusted baseline on real box scores.
Scope note: still conditioned on actual minutes (fixed-lineup stage); team-level
opponent defense (player-specific matchup adjustment comes with defender quality).

Usage: python scripts/52_sim_players_oppadj.py
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

RECENCY = {1: 5.0, 2: 4.0, 3: 3.0}
K = 1000.0
RK = 0.15          # team rating learning rate
REVERT = 0.25
HOME_FCT = 1.012   # home scoring boost; away = 2 - HOME_FCT


def project_rate(df, metric):
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
        pm = np.average(prior[metric].dropna(), weights=prior.loc[prior[metric].notna(), "MIN"])
        num = den = 0.0
        for lag, w in RECENCY.items():
            s = inv.get(ti - lag)
            v, m = val.get((r.PLAYER_ID, s)), mn.get((r.PLAYER_ID, s))
            if v is not None and m is not None and not np.isnan(v):
                num += w * m * v; den += w * m
        if den:
            out[(r.PLAYER_ID, r.SEASON)] = (num + K * pm) / (den + K)
    return out


def team_defense_going_in(games):
    """Leakage-free: each team's defensive rating (pts allowed above avg) BEFORE each game."""
    off = defaultdict(float); dfn = defaultdict(float)
    league = games.HOME_PTS.head(200).mean()
    prev = None
    face_def = {}   # (game_id, team_id) -> opponent's def rating faced
    league_at = {}
    for g in games.itertuples():
        if prev is not None and g.SEASON != prev:
            for t in list(off): off[t] *= (1 - REVERT)
            for t in list(dfn): dfn[t] *= (1 - REVERT)
        prev = g.SEASON
        h, a = g.HOME_TEAM_ID, g.AWAY_TEAM_ID
        face_def[(g.GAME_ID, h)] = dfn[a]   # home faces away's defense
        face_def[(g.GAME_ID, a)] = dfn[h]
        league_at[g.GAME_ID] = league
        mu_h = league + off[h] + dfn[a] + 2.8
        mu_a = league + off[a] + dfn[h]
        eh, ea = g.HOME_PTS - mu_h, g.AWAY_PTS - mu_a
        off[h] += RK * eh / 2; dfn[a] += RK * eh / 2
        off[a] += RK * ea / 2; dfn[h] += RK * ea / 2
        league += 0.01 * ((g.HOME_PTS + g.AWAY_PTS) / 2 - league)
    return face_def, league_at


def mae(p, a):
    return np.abs(np.asarray(p) - np.asarray(a)).mean()


def main() -> None:
    ps = pd.read_parquet(PS)
    proj = project_rate(ps, "PTS_36")

    games = pd.read_parquet(GAMES).sort_values(["GAME_DATE", "GAME_ID"])
    face_def, league_at = team_defense_going_in(games)
    home_team = {r.GAME_ID: r.HOME_TEAM_ID for r in games.itertuples()}

    pg = pd.read_parquet(PG, columns=["GAME_ID", "SEASON", "SEASON_TYPE", "TEAM_ID",
                                       "PLAYER_ID", "MIN", "points"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)].copy()
    pg["proj36"] = [proj.get((p, s), np.nan) for p, s in zip(pg.PLAYER_ID, pg.SEASON)]
    pg = pg.dropna(subset=["proj36"])

    pg["baseline"] = pg.proj36 * pg.MIN / 36.0

    fd = np.array([face_def.get((g, t), 0.0) for g, t in zip(pg.GAME_ID, pg.TEAM_ID)])
    lg = np.array([league_at.get(g, 110.0) for g in pg.GAME_ID])
    opp_factor = (lg + fd) / lg
    is_home = np.array([home_team.get(g) == t for g, t in zip(pg.GAME_ID, pg.TEAM_ID)])
    home_factor = np.where(is_home, HOME_FCT, 2 - HOME_FCT)

    pg["opp_adj"] = pg.baseline * opp_factor
    pg["opp_home_adj"] = pg.baseline * opp_factor * home_factor

    print(f"Player-games: {len(pg):,}\n")
    print("Points-per-game MAE (lower better):")
    print(f"  {'baseline (no opponent)':<28} {mae(pg.baseline, pg.points):.4f}")
    print(f"  {'+ opponent defense':<28} {mae(pg.opp_adj, pg.points):.4f}")
    print(f"  {'+ opponent + home/away':<28} {mae(pg.opp_home_adj, pg.points):.4f}  <- full")
    b, f = mae(pg.baseline, pg.points), mae(pg.opp_home_adj, pg.points)
    print(f"\n  improvement over baseline: {(1 - f/b)*100:.2f}%")

    # where the adjustment matters most: games vs the best/worst defenses
    pg["fd"] = fd
    hard = pg[pg.fd < np.percentile(fd, 15)]   # facing elite defenses
    easy = pg[pg.fd > np.percentile(fd, 85)]   # facing weak defenses
    print("\nSplit by opponent defense (does the sign make sense?):")
    print(f"  vs elite D: actual {hard.points.mean():.1f} ppg, "
          f"baseline {hard.baseline.mean():.1f} -> adjusted {hard.opp_home_adj.mean():.1f}")
    print(f"  vs weak D:  actual {easy.points.mean():.1f} ppg, "
          f"baseline {easy.baseline.mean():.1f} -> adjusted {easy.opp_home_adj.mean():.1f}")


if __name__ == "__main__":
    main()
