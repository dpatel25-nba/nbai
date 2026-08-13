"""
Layer-2 parser for hustlestatsboxscore -> player_hustle / team_hustle.

14,128 games of hustle data were pulled in the overnight run (script 95) and then
never parsed — the largest block of unused data in the project. It measures the
effort and defensive events box scores structurally cannot see:

    CONTESTED_SHOTS (2PT/3PT split)   DEFLECTIONS        CHARGES_DRAWN
    SCREEN_ASSISTS + points from them LOOSE_BALLS (off/def)
    BOX_OUTS (off/def) and the rebounds they produced

This matters because your notes repeatedly reach the same wall: "box stats can't
measure defense", and defender-quality v2 turned out descriptive-only. Deflections
and contested shots are direct observations of defensive activity, available per
game from 2015-16 on, so they can feed both the simulator's defensive terms and a
future defensive-value metric.

Availability is not universal — the feed reports HUSTLE_STATUS per game and some
games carry no rows — so coverage is reported rather than assumed.

Output: data/parquet/player_hustle.parquet, data/parquet/team_hustle.parquet
Usage: python scripts/127_build_hustle.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "hustle"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT_P = ROOT / "data" / "parquet" / "player_hustle.parquet"
OUT_T = ROOT / "data" / "parquet" / "team_hustle.parquet"

DROP = {"TEAM_CITY", "TEAM_NAME", "COMMENT"}


def rows_of(payload: dict, name: str) -> list[dict]:
    for rs in payload.get("resultSets", []):
        if isinstance(rs, dict) and rs.get("name") == name:
            hdr = rs.get("headers") or []
            return [dict(zip(hdr, r)) for r in rs.get("rowSet") or []]
    return []


def main() -> None:
    files = sorted(RAW.glob("*.json"))
    print(f"hustle files on disk: {len(files):,}")

    prows, trows, unavailable, broken = [], [], 0, 0
    for i, f in enumerate(files, 1):
        try:
            d = json.loads(f.read_text())
        except json.JSONDecodeError:
            broken += 1
            continue
        avail = rows_of(d, "HustleStatsAvailable")
        if avail and not avail[0].get("HUSTLE_STATUS"):
            unavailable += 1
        prows.extend(rows_of(d, "PlayerStats"))
        trows.extend(rows_of(d, "TeamStats"))
        if i % 2500 == 0:
            print(f"  parsed {i:,}/{len(files):,}", flush=True)

    players = pd.DataFrame(prows).drop(columns=list(DROP), errors="ignore")
    teams = pd.DataFrame(trows).drop(columns=list(DROP), errors="ignore")

    # the feed is inconsistent about ID types across games (some int, some str),
    # which makes the column object-dtype and unwritable to parquet
    for df in (players, teams):
        if "GAME_ID" in df.columns:
            df["GAME_ID"] = df["GAME_ID"].astype(str).str.zfill(10)
        for c in ("TEAM_ID", "PLAYER_ID"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
        for c in ("TEAM_ABBREVIATION", "PLAYER_NAME", "START_POSITION", "MINUTES"):
            if c in df.columns:
                df[c] = df[c].astype(str)

    # attach season / date so the tables join like every other Layer-2 table
    g = pd.read_parquet(GAMES, columns=["GAME_ID", "GAME_DATE", "SEASON", "SEASON_TYPE"])
    players = players.merge(g, on="GAME_ID", how="left")
    teams = teams.merge(g, on="GAME_ID", how="left")

    num = ["CONTESTED_SHOTS", "CONTESTED_SHOTS_2PT", "CONTESTED_SHOTS_3PT",
           "DEFLECTIONS", "CHARGES_DRAWN", "SCREEN_ASSISTS", "SCREEN_AST_PTS",
           "OFF_LOOSE_BALLS_RECOVERED", "DEF_LOOSE_BALLS_RECOVERED",
           "LOOSE_BALLS_RECOVERED", "OFF_BOXOUTS", "DEF_BOXOUTS",
           "BOX_OUT_PLAYER_TEAM_REBS", "BOX_OUT_PLAYER_REBS", "BOX_OUTS"]
    for df in (players, teams):
        for c in num:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

    players.to_parquet(OUT_P, index=False)
    teams.to_parquet(OUT_T, index=False)

    print(f"\nplayer_hustle: {len(players):,} rows x {players.shape[1]} cols "
          f"({players.GAME_ID.nunique():,} games)")
    print(f"team_hustle:   {len(teams):,} rows ({teams.GAME_ID.nunique():,} games)")
    if broken:
        print(f"unreadable files: {broken}")
    print(f"games flagged hustle-unavailable: {unavailable:,}")

    print("\nCoverage by season (games with player hustle rows):")
    cov = players.groupby("SEASON").GAME_ID.nunique()
    tot = g[g.SEASON >= "2015-16"].groupby("SEASON").GAME_ID.nunique()
    for s in sorted(cov.index.dropna()):
        print(f"  {s}: {cov[s]:>5,} / {tot.get(s, 0):>5,} games")

    print("\nFace check — 2024-25 leaders per game (min 40 games):")
    q = players[(players.SEASON == "2024-25") & (players.SEASON_TYPE == "Regular Season")]
    agg = q.groupby(["PLAYER_ID", "PLAYER_NAME"]).agg(
        g=("GAME_ID", "size"), defl=("DEFLECTIONS", "mean"),
        cont=("CONTESTED_SHOTS", "mean"), scr=("SCREEN_ASSISTS", "mean"),
        chg=("CHARGES_DRAWN", "sum")).reset_index()
    agg = agg[agg.g >= 40]
    for label, col in [("deflections/gm", "defl"), ("contested shots/gm", "cont"),
                       ("screen assists/gm", "scr"), ("charges drawn (total)", "chg")]:
        top = agg.nlargest(3, col)
        names = ", ".join(f"{r.PLAYER_NAME} {getattr(r, col):.1f}" for r in top.itertuples())
        print(f"  {label:<22} {names}")


if __name__ == "__main__":
    main()
