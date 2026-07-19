"""
Leakage-free player-game feature matrix for the player-props predictive study.

For every player-game, snapshot pre-game state: projected scoring rate (Marcel),
minutes, recent form (last-5/10 points & minutes & rate), the opponent's rolling
team defense, home/away, and rest. Target = actual points that game.

Output: data/parquet/player_game_features.parquet
Usage: python scripts/62_build_player_features.py
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT = ROOT / "data" / "parquet" / "player_game_features.parquet"

RECENCY = {1: 5.0, 2: 4.0, 3: 3.0}
K = 1000.0
RK = 0.15
REVERT = 0.25


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
    off = defaultdict(float); dfn = defaultdict(float)
    league = games.HOME_PTS.head(200).mean(); prev = None
    face = {}
    for g in games.itertuples():
        if prev is not None and g.SEASON != prev:
            for d in (off, dfn):
                for t in list(d): d[t] *= (1 - REVERT)
        prev = g.SEASON
        h, a = g.HOME_TEAM_ID, g.AWAY_TEAM_ID
        face[(g.GAME_ID, h)] = dfn[a]; face[(g.GAME_ID, a)] = dfn[h]
        mu_h = league + off[h] + dfn[a] + 2.8; mu_a = league + off[a] + dfn[h]
        eh, ea = g.HOME_PTS - mu_h, g.AWAY_PTS - mu_a
        off[h] += RK * eh / 2; dfn[a] += RK * eh / 2
        off[a] += RK * ea / 2; dfn[h] += RK * ea / 2
        league += 0.01 * ((g.HOME_PTS + g.AWAY_PTS) / 2 - league)
    return face


def main() -> None:
    ps = pd.read_parquet(PS)
    proj = project_rate(ps, "PTS_36")
    games = pd.read_parquet(GAMES).sort_values(["GAME_DATE", "GAME_ID"])
    face = team_defense_going_in(games)

    pg = pd.read_parquet(PG, columns=["GAME_ID", "GAME_DATE", "SEASON", "SEASON_TYPE",
                                       "TEAM_ID", "HOME_AWAY", "PLAYER_ID", "MIN", "points"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)].copy()
    pg = pg.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)

    hist = defaultdict(lambda: deque(maxlen=10))   # per player recent games
    lastdate = {}
    rows = []
    for r in pg.itertuples():
        pj = proj.get((r.PLAYER_ID, r.SEASON))
        if pj is None:
            hist[r.PLAYER_ID].append({"pts": r.points, "min": r.MIN,
                                      "p36": r.points / r.MIN * 36}); lastdate[r.PLAYER_ID] = r.GAME_DATE
            continue
        h = hist[r.PLAYER_ID]
        def m(k, n): return np.mean([e[k] for e in list(h)[-n:]]) if h else np.nan
        rest = (r.GAME_DATE - lastdate[r.PLAYER_ID]).days if r.PLAYER_ID in lastdate else 5
        rows.append({
            "SEASON": r.SEASON, "PLAYER_ID": r.PLAYER_ID, "points": r.points,
            "proj_pts36": pj, "min": r.MIN,
            "recent_pts5": m("pts", 5), "recent_pts10": m("pts", 10),
            "recent_min5": m("min", 5), "recent_p36_10": m("p36", 10),
            "opp_def": face.get((r.GAME_ID, r.TEAM_ID), 0.0),
            "home": int(r.HOME_AWAY == "HOME"),
            "rest": min(rest, 7), "b2b": int(rest == 1),
        })
        hist[r.PLAYER_ID].append({"pts": r.points, "min": r.MIN, "p36": r.points / r.MIN * 36})
        lastdate[r.PLAYER_ID] = r.GAME_DATE

    df = pd.DataFrame(rows).dropna().reset_index(drop=True)
    df.to_parquet(OUT, index=False)
    print(f"Wrote {len(df):,} player-games x {df.shape[1]} cols to {OUT}")
    print("features:", [c for c in df.columns if c not in ("SEASON", "PLAYER_ID", "points")])


if __name__ == "__main__":
    main()
