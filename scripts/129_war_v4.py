"""
WAR v4 — hustle defense folded into the defensive component.

The progression so far, judged on team-win correlation:
    v1  box only                              0.837
    v2  + synergy offence + rim protection    0.858
    v3  + playmaking creation                 0.868

Every step so far improved the OFFENSIVE side more than the defensive one, and
the defensive side is where the metric was always weakest — box scores barely
observe defense, and both the matchup metric (script 89) and, more mildly, rim
protection were limited. Script 128 changed that: deflections predict team
defensive rating better than rim protection or box DBPM, and hustle as a group
takes -6.2% off leave-one-season-out CV error.

v4 keeps v3's offence untouched and rebuilds only the defensive component:

    DBPM4 = box DBPM + 0.6*rim protection + 0.6*deflections + 0.25*(charges, box-outs)

Weights follow the same z-score blend convention as v2/v3 so the units stay
"BPM points per 100 possessions", and replacement level and points-per-win are
held constant across versions so only the impact core is being compared — the
discipline your bake-off notes insist on.

Hustle coverage starts 2016-17, so v4 exists only from then on; earlier seasons
fall back to v3 rather than being silently dropped.

Output: data/parquet/player_seasons_war_v4.parquet
Usage: python scripts/129_war_v4.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "data" / "parquet" / "player_synergy.parquet"
TRK = ROOT / "data" / "parquet" / "player_tracking.parquet"
HUS = ROOT / "data" / "parquet" / "player_hustle.parquet"
WAR = ROOT / "data" / "parquet" / "player_seasons_war.parquet"
V2 = ROOT / "data" / "parquet" / "player_seasons_war_v2.parquet"
V3 = ROOT / "data" / "parquet" / "player_seasons_war_v3.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT = ROOT / "data" / "parquet" / "player_seasons_war_v4.parquet"

REP, VW, ANCHOR = -2.0, 2.7, 490.0
FIRST_HUSTLE = "2016-17"


def zc(s):
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd else s * 0


def main() -> None:
    # ---- offence: unchanged from v3 (synergy + creation) ----
    syn = pd.read_parquet(SYN)
    off = syn[syn.side == "offensive"].copy()
    lg = (off.groupby(["SEASON", "play_type"])
             .apply(lambda g: np.average(g.ppp.fillna(0), weights=g.poss.fillna(0) + 1e-9),
                    include_groups=False).rename("lg").reset_index())
    off = off.merge(lg, on=["SEASON", "play_type"])
    off["v"] = off.poss.fillna(0) * (off.ppp.fillna(0) - off.lg)
    synv = off.groupby(["PLAYER_ID", "SEASON"]).v.sum().rename("syn").reset_index()

    trk = pd.read_parquet(TRK)
    trk = trk[trk.MIN > 0].copy()
    lgr = (trk.dropna(subset=["DEF_RIM_FG_PCT", "DEF_RIM_FGA"]).groupby("SEASON")
              .apply(lambda g: np.average(g.DEF_RIM_FG_PCT, weights=g.DEF_RIM_FGA + 1e-9),
                     include_groups=False).rename("lgr").reset_index())
    trk = trk.merge(lgr, on="SEASON", how="left")
    trk["rim"] = trk.DEF_RIM_FGA.fillna(0) * (trk.lgr - trk.DEF_RIM_FG_PCT.fillna(trk.lgr))
    trk["crea"] = trk.AST_POINTS_CREATED.fillna(0) + 0.5 * trk.POTENTIAL_AST.fillna(0)

    # ---- new: hustle defensive activity, per 36 ----
    h = pd.read_parquet(HUS)
    h = h[h.SEASON_TYPE == "Regular Season"].copy()
    h["mins"] = pd.to_numeric(h.MINUTES.astype(str).str.split(":").str[0],
                              errors="coerce").fillna(0)
    hg = h.groupby(["PLAYER_ID", "SEASON"]).agg(
        mins=("mins", "sum"), defl=("DEFLECTIONS", "sum"),
        chg=("CHARGES_DRAWN", "sum"), dbox=("DEF_BOXOUTS", "sum")).reset_index()
    hg = hg[hg.mins >= 200].copy()
    for c in ("defl", "chg", "dbox"):
        hg[c + "36"] = hg[c] / hg.mins * 36
    hg["PLAYER_ID"] = hg.PLAYER_ID.astype("int64")

    m = (pd.read_parquet(WAR)
         .merge(synv, on=["PLAYER_ID", "SEASON"], how="left")
         .merge(trk[["PLAYER_ID", "SEASON", "rim", "crea"]],
                on=["PLAYER_ID", "SEASON"], how="left")
         .merge(hg[["PLAYER_ID", "SEASON", "defl36", "chg36", "dbox36"]],
                on=["PLAYER_ID", "SEASON"], how="left"))
    for c in ["syn", "rim", "crea", "defl36", "chg36", "dbox36"]:
        m[c] = m[c].fillna(0.0)

    zoff = (m.groupby("SEASON").OBPM.transform(zc)
            + 0.6 * m.groupby("SEASON").syn.transform(zc)
            + 0.5 * m.groupby("SEASON").crea.transform(zc))
    zdef = (m.groupby("SEASON").DBPM.transform(zc)
            + 0.6 * m.groupby("SEASON").rim.transform(zc)
            + 0.6 * m.groupby("SEASON").defl36.transform(zc)
            + 0.25 * m.groupby("SEASON").chg36.transform(zc)
            + 0.25 * m.groupby("SEASON").dbox36.transform(zc))
    m["OBPM4"] = zoff / zoff.std(ddof=0) * m.OBPM.std(ddof=0) + m.OBPM.mean()
    m["DBPM4"] = zdef / zdef.std(ddof=0) * m.DBPM.std(ddof=0) + m.DBPM.mean()
    raw = m.OBPM4 + m.DBPM4
    center = m.groupby("SEASON").apply(
        lambda g: np.average(g.OBPM4 + g.DBPM4, weights=g.MIN),
        include_groups=False).reindex(m.SEASON).values
    m["BPM4"] = raw - center

    share = m.MIN / (5 * 48 * m.GP)
    warr = (m.BPM4 - REP) * share * (m.GP / 82.0) * VW
    scale = ANCHOR / warr.groupby(m.SEASON).sum().mean()
    m["WAR4"] = (warr * scale).round(2)
    m[["PLAYER_ID", "SEASON", "PLAYER", "TEAM", "MPG",
       "OBPM4", "DBPM4", "BPM4", "WAR4"]].to_parquet(OUT, index=False)

    # ---- bottom line: team WAR vs actual wins, all four versions ----
    v2 = pd.read_parquet(V2)[["PLAYER_ID", "SEASON", "WAR2"]]
    v3 = pd.read_parquet(V3)[["PLAYER_ID", "SEASON", "WAR3"]]
    m = m.merge(v2, on=["PLAYER_ID", "SEASON"], how="left") \
         .merge(v3, on=["PLAYER_ID", "SEASON"], how="left")
    rs = pd.read_parquet(GAMES)
    rs = rs[rs.SEASON_TYPE == "Regular Season"]
    wins = {}
    for r in rs.itertuples():
        for t in (r.HOME_TEAM, r.AWAY_TEAM):
            wins.setdefault((t, r.SEASON), 0)
        wins[(r.HOME_TEAM if r.HOME_WIN else r.AWAY_TEAM, r.SEASON)] += 1
    tw = m.groupby(["TEAM", "SEASON"]).agg(
        w1=("WAR", "sum"), w2=("WAR2", "sum"),
        w3=("WAR3", "sum"), w4=("WAR4", "sum")).reset_index()
    tw["wins"] = [wins.get((t, s), np.nan) for t, s in zip(tw.TEAM, tw.SEASON)]
    tw = tw.dropna()
    # compare on the seasons where hustle exists, so v4 is not credited or
    # penalised for rows it could never have improved
    hu = tw[tw.SEASON >= FIRST_HUSTLE]

    print(f"WAR v4 (hustle defense) — {len(m):,} player-seasons\n")
    print(f"Team WAR vs actual wins, hustle era only ({len(hu)} team-seasons, "
          f"{hu.SEASON.min()}…{hu.SEASON.max()}):")
    for lbl, col in [("v1 box only", "w1"), ("v2 +synergy +rim", "w2"),
                     ("v3 +creation", "w3"), ("v4 +hustle defense", "w4")]:
        print(f"  {lbl:<22} corr {np.corrcoef(hu[col], hu.wins)[0, 1]:+.3f}")
    print(f"\nAll seasons ({len(tw)} team-seasons), v3 vs v4:")
    print(f"  v3 {np.corrcoef(tw.w3, tw.wins)[0, 1]:+.3f}   "
          f"v4 {np.corrcoef(tw.w4, tw.wins)[0, 1]:+.3f}")

    print("\nTop 12 by WAR v4, 2024-25:")
    for r in m[m.SEASON == "2024-25"].nlargest(12, "WAR4").itertuples():
        print(f"  {r.PLAYER:<24} {r.TEAM:<4} WAR4 {r.WAR4:5.1f}  "
              f"(O {r.OBPM4:+.1f} D {r.DBPM4:+.1f})")
    print("\nBiggest defensive risers vs v3 (2024-25):")
    q = m[(m.SEASON == "2024-25") & (m.MIN >= 1200)].copy()
    v3d = pd.read_parquet(V3)[["PLAYER_ID", "SEASON", "DBPM3"]]
    q = q.merge(v3d, on=["PLAYER_ID", "SEASON"], how="left")
    q["d"] = q.DBPM4 - q.DBPM3
    for r in q.nlargest(6, "d").itertuples():
        print(f"  {r.PLAYER:<24} {r.TEAM:<4} DBPM {r.DBPM3:+.1f} -> {r.DBPM4:+.1f}")


if __name__ == "__main__":
    main()
