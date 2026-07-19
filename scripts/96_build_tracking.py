"""
Layer-2 parser for leaguedashptstats -> player_tracking.parquet

Merges all 11 tracking measure types (Player files) into one wide player-season
table: shot diet (drives / catch&shoot / pull-up), touches & time of possession,
passing creation, rebounding chances, rim defense, speed/distance. PerGame mode.

Output: data/parquet/player_tracking.parquet
Usage: python scripts/96_build_tracking.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "ptstats"
OUT = ROOT / "data" / "parquet" / "player_tracking.parquet"

SEASONS = [f"{y}-{str(y + 1)[2:]}" for y in range(2013, 2026)]
MEASURES = ["Possessions", "Drives", "CatchShoot", "PullUpShot", "Passing",
            "Rebounding", "Defense", "SpeedDistance", "ElbowTouch", "PostTouch", "PaintTouch"]
COMMON = {"PLAYER_NAME", "TEAM_ID", "TEAM_ABBREVIATION", "W", "L"}


def load(season, measure):
    f = RAW / f"{season}_{measure}_Player.json"
    if not f.exists():
        return None
    rs = json.loads(f.read_text())["resultSets"][0]
    df = pd.DataFrame(rs["rowSet"], columns=rs["headers"])
    return df.drop(columns=[c for c in COMMON if c in df.columns])


def main() -> None:
    rows = []
    for season in SEASONS:
        acc = None
        for m in MEASURES:
            df = load(season, m)
            if df is None:
                continue
            if acc is None:
                acc = df
            else:
                # only bring columns not already present (dedupes TOUCHES, GP, MIN…)
                new = [c for c in df.columns if c not in acc.columns or c == "PLAYER_ID"]
                acc = acc.merge(df[new], on="PLAYER_ID", how="outer")
        if acc is not None:
            acc["SEASON"] = season
            rows.append(acc)

    out = pd.concat(rows, ignore_index=True)
    out.to_parquet(OUT, index=False)
    print(f"player_tracking.parquet: {len(out):,} player-seasons x {out.shape[1]} cols, "
          f"{out.SEASON.min()}…{out.SEASON.max()}")
    # quick face check — shot diet of a few known player-seasons
    show = ["DRIVES", "CATCH_SHOOT_FGA", "PULL_UP_FGA", "TOUCHES", "TIME_OF_POSS",
            "AVG_SEC_PER_TOUCH", "PTS_PER_TOUCH", "POST_TOUCHES"]
    latest = out[out.SEASON == "2024-25"]
    for pid, name in [(201939, "Curry"), (203999, "Jokic"), (1628983, "SGA")]:
        r = latest[latest.PLAYER_ID == pid]
        if len(r):
            r = r.iloc[0]
            print(f"\n{name} 2024-25:")
            for c in show:
                if c in r.index:
                    print(f"   {c:<20} {r[c]}")


if __name__ == "__main__":
    main()
