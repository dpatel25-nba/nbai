"""
Clutch performance metric — from play-by-play.

Clutch = last 5 minutes of Q4/OT with the score within 5 points (the NBA
definition). For each player-season we measure their field-goal shooting in the
clutch and compare it to their overall season eFG% — do they elevate or shrink?

Output: data/parquet/clutch.parquet
Usage: python scripts/82_clutch.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PBP = ROOT / "data" / "parquet" / "pbp"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
OUT = ROOT / "data" / "parquet" / "clutch.parquet"


def main() -> None:
    pbp = pd.read_parquet(PBP, columns=["SEASON", "PERIOD", "SEC_REMAINING", "MARGIN",
                                        "PLAYER_ID", "PLAYER_NAME", "IS_FIELD_GOAL",
                                        "SHOT_RESULT", "SHOT_VALUE"])
    c = pbp[(pbp.IS_FIELD_GOAL == 1) & (pbp.PERIOD >= 4) & (pbp.SEC_REMAINING <= 300)
            & (pbp.MARGIN.abs() <= 5) & pbp.SHOT_RESULT.isin(["Made", "Missed"])
            & pbp.SHOT_VALUE.isin([2, 3]) & pbp.PLAYER_ID.notna()].copy()
    c["made"] = (c.SHOT_RESULT == "Made").astype(int)
    c["pts"] = c.made * c.SHOT_VALUE
    c["fg3m"] = ((c.SHOT_VALUE == 3) & (c.made == 1)).astype(int)

    g = c.groupby(["PLAYER_ID", "SEASON"]).agg(
        PLAYER=("PLAYER_NAME", "first"), cFGA=("made", "size"), cFGM=("made", "sum"),
        cFG3M=("fg3m", "sum"), cPTS=("pts", "sum")).reset_index()
    g["cEFG"] = ((g.cFGM + 0.5 * g.cFG3M) / g.cFGA).round(3)

    ps = pd.read_parquet(PS)[["PLAYER_ID", "SEASON", "EFG_PCT", "TEAM"]]
    g = g.merge(ps, on=["PLAYER_ID", "SEASON"], how="left")
    g["clutch_delta"] = (g.cEFG - g.EFG_PCT).round(3)   # + = shoots better when it matters
    g.to_parquet(OUT, index=False)

    print(f"Clutch metric — {len(g):,} player-seasons with clutch FGAs\n")
    q = g[g.cFGA >= 25]
    print("Most clutch points (single season, >=25 clutch FGA):")
    print(q.nlargest(10, "cPTS")[["PLAYER", "TEAM", "SEASON", "cFGA", "cPTS", "cEFG"]].to_string(index=False))
    print("\nBest clutch SHOOTING lift (clutch eFG - season eFG, >=40 clutch FGA):")
    top = g[g.cFGA >= 40].nlargest(8, "clutch_delta")
    print(top[["PLAYER", "TEAM", "SEASON", "cFGA", "cEFG", "EFG_PCT", "clutch_delta"]].to_string(index=False))
    print("\nBiggest clutch SHRINK (choke, >=40 clutch FGA):")
    bot = g[g.cFGA >= 40].nsmallest(6, "clutch_delta")
    print(bot[["PLAYER", "TEAM", "SEASON", "cFGA", "cEFG", "EFG_PCT", "clutch_delta"]].to_string(index=False))


if __name__ == "__main__":
    main()
