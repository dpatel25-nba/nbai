"""
WAR v3 — offense = box OBPM + synergy efficiency + playmaking creation;
         defense = box DBPM + rim protection.

Creation (script 112) adds out-of-sample offensive signal on top of synergy, so
fold it in. Blend components on a per-season z-scale, rescale to BPM units, rebuild
WAR, and re-test the bottom line: team-win correlation vs v1 (box) and v2 (box+
synergy+rim). Output: player_seasons_war_v3.parquet.

Usage: python scripts/113_war_v3.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "data" / "parquet" / "player_synergy.parquet"
TRK = ROOT / "data" / "parquet" / "player_tracking.parquet"
WAR = ROOT / "data" / "parquet" / "player_seasons_war.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT = ROOT / "data" / "parquet" / "player_seasons_war_v3.parquet"


def zc(s):
    return (s - s.mean()) / s.std(ddof=0) if s.std(ddof=0) else s * 0


def main() -> None:
    syn = pd.read_parquet(SYN); off = syn[syn.side == "offensive"].copy()
    lg = (off.groupby(["SEASON", "play_type"])
             .apply(lambda g: np.average(g.ppp.fillna(0), weights=g.poss.fillna(0) + 1e-9),
                    include_groups=False).rename("lg").reset_index())
    off = off.merge(lg, on=["SEASON", "play_type"])
    off["v"] = off.poss.fillna(0) * (off.ppp.fillna(0) - off.lg)
    synv = off.groupby(["PLAYER_ID", "SEASON"]).v.sum().rename("syn").reset_index()

    trk = pd.read_parquet(TRK); trk = trk[trk.MIN > 0].copy()
    lgr = (trk.dropna(subset=["DEF_RIM_FG_PCT", "DEF_RIM_FGA"]).groupby("SEASON")
              .apply(lambda g: np.average(g.DEF_RIM_FG_PCT, weights=g.DEF_RIM_FGA + 1e-9),
                     include_groups=False).rename("lgr").reset_index())
    trk = trk.merge(lgr, on="SEASON", how="left")
    trk["rim"] = trk.DEF_RIM_FGA.fillna(0) * (trk.lgr - trk.DEF_RIM_FG_PCT.fillna(trk.lgr))
    trk["crea"] = trk.AST_POINTS_CREATED.fillna(0) + 0.5 * trk.POTENTIAL_AST.fillna(0)

    m = (pd.read_parquet(WAR)
         .merge(synv, on=["PLAYER_ID", "SEASON"], how="left")
         .merge(trk[["PLAYER_ID", "SEASON", "rim", "crea"]], on=["PLAYER_ID", "SEASON"], how="left"))
    for c in ["syn", "rim", "crea"]:
        m[c] = m[c].fillna(0.0)

    # offense: box OBPM + synergy + creation ; defense: box DBPM + rim
    zoff = (m.groupby("SEASON").OBPM.transform(zc) + 0.6 * m.groupby("SEASON").syn.transform(zc)
            + 0.5 * m.groupby("SEASON").crea.transform(zc))
    zdef = m.groupby("SEASON").DBPM.transform(zc) + 0.6 * m.groupby("SEASON").rim.transform(zc)
    m["OBPM3"] = zoff / zoff.std(ddof=0) * m.OBPM.std(ddof=0) + m.OBPM.mean()
    m["DBPM3"] = zdef / zdef.std(ddof=0) * m.DBPM.std(ddof=0) + m.DBPM.mean()
    raw = m.OBPM3 + m.DBPM3
    center = m.groupby("SEASON").apply(lambda g: np.average(
        (g.OBPM3 + g.DBPM3), weights=g.MIN), include_groups=False).reindex(m.SEASON).values
    m["BPM3"] = raw - center

    REP, VW = -2.0, 2.7
    share = m.MIN / (5 * 48 * m.GP)
    warr = (m.BPM3 - REP) * share * (m.GP / 82.0) * VW
    scale = 490.0 / warr.groupby(m.SEASON).sum().mean()
    m["WAR3"] = (warr * scale).round(2)
    m[["PLAYER_ID", "SEASON", "PLAYER", "TEAM", "MPG", "OBPM3", "DBPM3", "BPM3", "WAR3"]].to_parquet(OUT, index=False)

    # team WAR vs actual wins, all three versions
    v2 = pd.read_parquet(ROOT / "data" / "parquet" / "player_seasons_war_v2.parquet")[["PLAYER_ID", "SEASON", "WAR2"]]
    m = m.merge(v2, on=["PLAYER_ID", "SEASON"], how="left")
    rs = pd.read_parquet(GAMES); rs = rs[rs.SEASON_TYPE == "Regular Season"]
    wins = {}
    for r in rs.itertuples():
        for t in (r.HOME_TEAM, r.AWAY_TEAM):
            wins.setdefault((t, r.SEASON), 0)
        wins[(r.HOME_TEAM if r.HOME_WIN else r.AWAY_TEAM, r.SEASON)] += 1
    tw = m.groupby(["TEAM", "SEASON"]).agg(w1=("WAR", "sum"), w2=("WAR2", "sum"), w3=("WAR3", "sum")).reset_index()
    tw["wins"] = [wins.get((t, s), np.nan) for t, s in zip(tw.TEAM, tw.SEASON)]
    tw = tw.dropna()
    print(f"WAR v3 (box+synergy+creation offense, box+rim defense) — {len(m):,} player-seasons\n")
    print(f"Team WAR vs actual wins ({len(tw)} team-seasons):")
    print(f"  v1 box only              corr {np.corrcoef(tw.w1, tw.wins)[0,1]:+.3f}")
    print(f"  v2 +synergy +rim         corr {np.corrcoef(tw.w2, tw.wins)[0,1]:+.3f}")
    print(f"  v3 +creation             corr {np.corrcoef(tw.w3, tw.wins)[0,1]:+.3f}")
    print("\nTop 12 by WAR v3, 2024-25:")
    for r in m[m.SEASON == "2024-25"].nlargest(12, "WAR3").itertuples():
        print(f"  {r.PLAYER:<24} {r.TEAM:<4} WAR3 {r.WAR3:5.1f}  (O {r.OBPM3:+.1f} D {r.DBPM3:+.1f})")


if __name__ == "__main__":
    main()
