"""
Build the Layer 2 play-by-play table from raw per-game JSON.

Output: data/parquet/pbp/<season>.parquet  (one file per season, each carrying a
SEASON column). Read one season with pd.read_parquet('.../pbp/2015-16.parquet')
or the whole dataset with pd.read_parquet('.../pbp/'). One row per event, with
clean join keys, parsed clock, shot geometry, and a reliable running score.

Idempotent: rebuilds each season from whatever games have a pbp file on disk, so
re-run after more scraping. Seasons with no pbp files yet are skipped.

Usage: python scripts/21_build_pbp.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "playbyplayv3"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT_DIR = ROOT / "data" / "parquet" / "pbp"

_CLOCK = re.compile(r"PT(\d+)M([\d.]+)S")


def clock_seconds(s) -> float | None:
    """ISO period clock 'PT11M34.00S' -> seconds remaining in the period."""
    if not s:
        return None
    m = _CLOCK.search(str(s))
    if not m:
        return None
    return int(m.group(1)) * 60 + float(m.group(2))


def num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def build_game(actions: list, meta) -> list[dict]:
    rows = []
    for a in actions:
        rows.append({
            "GAME_ID": meta.GAME_ID,
            "GAME_DATE": meta.GAME_DATE,
            "SEASON": meta.SEASON,
            "SEASON_TYPE": meta.SEASON_TYPE,
            "PERIOD": a.get("period"),
            "ACTION_NUMBER": a.get("actionNumber"),
            "SEC_REMAINING": clock_seconds(a.get("clock")),
            "TEAM_ID": a.get("teamId") or None,
            "TEAM_TRICODE": a.get("teamTricode") or None,
            "PLAYER_ID": a.get("personId") or None,
            "PLAYER_NAME": a.get("playerName") or None,
            "ACTION_TYPE": a.get("actionType"),
            "SUB_TYPE": a.get("subType"),
            "DESCRIPTION": a.get("description"),
            "IS_FIELD_GOAL": a.get("isFieldGoal"),
            "SHOT_RESULT": a.get("shotResult") or None,
            "SHOT_VALUE": num(a.get("shotValue")),
            "SHOT_DISTANCE": num(a.get("shotDistance")),
            "SHOT_X": num(a.get("xLegacy")),
            "SHOT_Y": num(a.get("yLegacy")),
            "LOCATION": a.get("location") or None,
            "SCORE_HOME_RAW": num(a.get("scoreHome")),
            "SCORE_AWAY_RAW": num(a.get("scoreAway")),
        })
    return rows


def main() -> None:
    games = pd.read_parquet(GAMES)
    meta = {r.GAME_ID: r for r in games.itertuples()}
    by_season: dict[str, list[str]] = {}
    for gid, m in meta.items():
        if (RAW / f"{gid}.json").exists():
            by_season.setdefault(m.SEASON, []).append(gid)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total_rows = 0
    print(f"Seasons with pbp on disk: {len(by_season)}")

    for season in sorted(by_season):
        rows = []
        for gid in sorted(by_season[season]):
            with open(RAW / f"{gid}.json") as f:
                actions = json.load(f)["game"]["actions"]
            rows.extend(build_game(actions, meta[gid]))

        df = pd.DataFrame(rows)
        df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"])
        # reliable running score: cumulative max per game (monotonic, ignores
        # stale scores on marker rows / zeros on non-scoring events)
        df = df.sort_values(["GAME_ID", "ACTION_NUMBER"])
        df["SCORE_HOME"] = df.groupby("GAME_ID")["SCORE_HOME_RAW"].cummax()
        df["SCORE_AWAY"] = df.groupby("GAME_ID")["SCORE_AWAY_RAW"].cummax()
        df["MARGIN"] = df["SCORE_HOME"] - df["SCORE_AWAY"]
        df = df.drop(columns=["SCORE_HOME_RAW", "SCORE_AWAY_RAW"])

        df.to_parquet(OUT_DIR / f"{season}.parquet", index=False)
        total_rows += len(df)
        print(f"  {season}: {len(by_season[season]):>4} games -> {len(df):>8,} events")

    print(f"\nWrote {total_rows:,} events across {len(by_season)} seasons to {OUT_DIR}/")


if __name__ == "__main__":
    main()
