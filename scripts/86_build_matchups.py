"""
Layer-2 parser for boxscorematchupsv3 -> matchups.parquet

Each raw file lists, per team, every defender and the offensive players they
guarded, with partial possessions and what the offender produced in that matchup.
We flatten to one row per (game, defender, offender):

  GAME_ID, SEASON, DEF_ID, OFF_ID, partial_poss, matchup_min,
  pts_allowed, fgm/fga, fg3m/fg3a, ftm/fta, ast, tov, blk, sfoul

This is the raw material for opponent-adjusted defender quality and, later,
player-specific matchup features for props.

Usage: python scripts/86_build_matchups.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "boxscorematchupsv3"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT = ROOT / "data" / "parquet" / "matchups.parquet"


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    season_of = {}
    g = pd.read_parquet(GAMES, columns=["GAME_ID", "SEASON", "SEASON_TYPE"])
    for r in g.itertuples():
        season_of[r.GAME_ID] = (r.SEASON, r.SEASON_TYPE)

    rows = []
    files = sorted(RAW.glob("*.json"))
    for i, f in enumerate(files):
        gid = f.stem
        meta = season_of.get(gid)
        if meta is None:
            continue
        season, stype = meta
        try:
            d = json.loads(f.read_text())["boxScoreMatchups"]
        except (json.JSONDecodeError, KeyError):
            continue
        for side in ("homeTeam", "awayTeam"):
            for defender in d[side]["players"]:
                did = defender["personId"]
                for m in defender.get("matchups", []) or []:
                    s = m["statistics"]
                    pp = num(s.get("partialPossessions"))
                    if pp <= 0:
                        continue
                    rows.append({
                        "GAME_ID": gid, "SEASON": season, "SEASON_TYPE": stype,
                        "DEF_ID": did, "OFF_ID": m["personId"],
                        "partial_poss": pp, "matchup_min": num(s.get("matchupMinutesSort")),
                        "pts_allowed": num(s.get("playerPoints")),
                        "fgm": num(s.get("matchupFieldGoalsMade")),
                        "fga": num(s.get("matchupFieldGoalsAttempted")),
                        "fg3m": num(s.get("matchupThreePointersMade")),
                        "fg3a": num(s.get("matchupThreePointersAttempted")),
                        "ftm": num(s.get("matchupFreeThrowsMade")),
                        "fta": num(s.get("matchupFreeThrowsAttempted")),
                        "ast": num(s.get("matchupAssists")),
                        "tov": num(s.get("matchupTurnovers")),
                        "blk": num(s.get("matchupBlocks")),
                        "sfoul": num(s.get("shootingFouls")),
                    })
        if (i + 1) % 1000 == 0:
            print(f"  parsed {i + 1:,}/{len(files):,} games…")

    df = pd.DataFrame(rows)
    df.to_parquet(OUT, index=False)
    print(f"\nmatchups.parquet: {len(df):,} defender-offender rows, "
          f"{df.GAME_ID.nunique():,} games, seasons {df.SEASON.min()}…{df.SEASON.max()}")
    print(f"  total partial possessions: {df.partial_poss.sum():,.0f}")


if __name__ == "__main__":
    main()
