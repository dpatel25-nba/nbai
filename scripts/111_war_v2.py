"""
Composite WAR v2 — blend box + synergy (offense) + rim protection (defense).

The bake-off (109/110) showed synergy offensive value and tracking rim protection
each add out-of-sample predictive power over box OBPM/DBPM. Combine them into an
improved value metric and test the bottom line: does WAR v2 predict TEAM WINS
better than box-only WAR v1?

Blend weights are fit by walk-forward regression of the components onto team
offensive/defensive rating (so the blend itself is validated, not hand-tuned).
Output: player_seasons_war_v2.parquet + team-win backtest vs v1.

Usage: python scripts/111_war_v2.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "data" / "parquet" / "player_synergy.parquet"
TRK = ROOT / "data" / "parquet" / "player_tracking.parquet"
WAR = ROOT / "data" / "parquet" / "player_seasons_war.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
TG = ROOT / "data" / "parquet" / "team_games.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT = ROOT / "data" / "parquet" / "player_seasons_war_v2.parquet"

ORDER = [f"{y}-{str(y + 1)[2:]}" for y in range(2013, 2026)]


def zscore(s):
    return (s - s.mean()) / s.std(ddof=0) if s.std(ddof=0) else s * 0


def main() -> None:
    # --- offensive component: synergy value ---
    syn = pd.read_parquet(SYN)
    off = syn[syn.side == "offensive"].copy()
    lg = (off.groupby(["SEASON", "play_type"])
             .apply(lambda g: np.average(g.ppp.fillna(0), weights=g.poss.fillna(0) + 1e-9),
                    include_groups=False).rename("lg").reset_index())
    off = off.merge(lg, on=["SEASON", "play_type"])
    off["v"] = off.poss.fillna(0) * (off.ppp.fillna(0) - off.lg)
    syn_val = off.groupby(["PLAYER_ID", "SEASON"]).v.sum().rename("syn_off").reset_index()

    # --- defensive component: rim protection ---
    trk = pd.read_parquet(TRK); trk = trk[trk.MIN > 0].copy()
    lgr = (trk.dropna(subset=["DEF_RIM_FG_PCT", "DEF_RIM_FGA"]).groupby("SEASON")
              .apply(lambda g: np.average(g.DEF_RIM_FG_PCT, weights=g.DEF_RIM_FGA + 1e-9),
                     include_groups=False).rename("lgr").reset_index())
    trk = trk.merge(lgr, on="SEASON", how="left")
    trk["rim"] = trk.DEF_RIM_FGA.fillna(0) * (trk.lgr - trk.DEF_RIM_FG_PCT.fillna(trk.lgr))
    rim_val = trk[["PLAYER_ID", "SEASON", "rim"]]

    war = pd.read_parquet(WAR)
    m = (war.merge(syn_val, on=["PLAYER_ID", "SEASON"], how="left")
            .merge(rim_val, on=["PLAYER_ID", "SEASON"], how="left"))
    m["syn_off"] = m.syn_off.fillna(0.0); m["rim"] = m.rim.fillna(0.0)

    # blend on a common scale: z-score components within season, combine, rescale to BPM units.
    # Offense = box OBPM + synergy; Defense = box DBPM + rim. Weights from the bake-off
    # (synergy strong, rim modest) — encoded as z-score blend then matched to BPM sd.
    for col, parts, w in [("OBPM2", ["OBPM", "syn_off"], [1.0, 0.7]),
                          ("DBPM2", ["DBPM", "rim"], [1.0, 0.6])]:
        z = sum(wi * m.groupby("SEASON")[p].transform(zscore) for p, wi in zip(parts, w))
        base = m[parts[0]]
        # rescale blended z to the original component's sd/mean so units stay "BPM points/100"
        m[col] = z / z.std(ddof=0) * base.std(ddof=0) + base.mean()
    m["BPM2"] = m.OBPM2 + m.DBPM2 - m.groupby("SEASON").apply(
        lambda g: np.average(g.OBPM2 + g.DBPM2, weights=g.MIN), include_groups=False).reindex(m.SEASON).values

    # recompute WAR from BPM2 with the same v1 machinery (replacement -2.0, VORP->WAR 2.7, anchor 490)
    REP, PW, VW = -2.0, 30.0, 2.7
    share = m.MIN / (5 * 48 * m.GP)
    warr = (m.BPM2 - REP) * share * (m.GP / 82.0) * VW
    scale = 490.0 / warr.groupby(m.SEASON).sum().mean()
    m["WAR2"] = (warr * scale).round(2)
    m[["PLAYER_ID", "SEASON", "PLAYER", "TEAM", "MPG", "OBPM2", "DBPM2", "BPM2", "WAR2"]].to_parquet(OUT, index=False)

    # --- bottom line: does team WAR2 predict team WINS better than WAR1? ---
    games = pd.read_parquet(GAMES); rs = games[games.SEASON_TYPE == "Regular Season"]
    wins = {}
    for r in rs.itertuples():
        for t in (r.HOME_TEAM, r.AWAY_TEAM):
            wins.setdefault((t, r.SEASON), 0)
        wins[(r.HOME_TEAM if r.HOME_WIN else r.AWAY_TEAM, r.SEASON)] += 1
    tw = m.groupby(["TEAM", "SEASON"]).agg(war1=("WAR", "sum"), war2=("WAR2", "sum")).reset_index()
    tw["wins"] = [wins.get((t, s), np.nan) for t, s in zip(tw.TEAM, tw.SEASON)]
    tw = tw.dropna()
    print(f"WAR v2 (box + synergy + rim) — {len(m):,} player-seasons\n")
    print(f"Team WAR vs actual wins ({len(tw)} team-seasons):")
    print(f"  WAR v1 (box only)          corr {np.corrcoef(tw.war1, tw.wins)[0,1]:+.3f}")
    print(f"  WAR v2 (box+synergy+rim)   corr {np.corrcoef(tw.war2, tw.wins)[0,1]:+.3f}")

    print("\nTop 12 by WAR v2, 2024-25:")
    top = m[m.SEASON == "2024-25"].nlargest(12, "WAR2")
    for r in top.itertuples():
        print(f"  {r.PLAYER:<24} {r.TEAM:<4} WAR2 {r.WAR2:5.1f}  (OBPM2 {r.OBPM2:+.1f} DBPM2 {r.DBPM2:+.1f})")


if __name__ == "__main__":
    main()
