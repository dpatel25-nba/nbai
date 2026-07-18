"""
Build the game-level fact table (Layer 2): one row per game, home/away pivoted.

Source is game_logs.parquet, which has two rows per game (one per team). We use
the MATCHUP field to tell home from away ('X vs. Y' = home, 'X @ Y' = away) and
collapse to a single row with home/away team ids, points, and the outcome.

This is the workhorse table for game-level prediction (winner, margin, total).
Output: data/parquet/games.parquet
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GAME_LOGS = ROOT / "data" / "parquet" / "game_logs.parquet"
OUT = ROOT / "data" / "parquet" / "games.parquet"


def main() -> None:
    df = pd.read_parquet(GAME_LOGS)

    # 'vs.' => home team, '@' => away team.
    df["IS_HOME"] = df["MATCHUP"].str.contains("vs.", regex=False)

    # Neutral-site games (NBA Cup / international) mark BOTH teams '@', so no row
    # says 'vs.'. Flag them and assign home/away deterministically (by lower
    # TEAM_ID) — the label is arbitrary at a neutral site, and we zero out HCA
    # for these in the model.
    home_count = df.groupby("GAME_ID")["IS_HOME"].transform("sum")
    df["NEUTRAL"] = (home_count != 1).astype(int)
    df["_rank"] = df.groupby("GAME_ID")["TEAM_ID"].rank(method="first")
    df["HOME_FLAG"] = df["IS_HOME"]
    fallback = home_count != 1
    df.loc[fallback, "HOME_FLAG"] = df.loc[fallback, "_rank"] == 1

    keep = ["GAME_ID", "GAME_DATE", "SEASON", "SEASON_TYPE", "NEUTRAL",
            "TEAM_ID", "TEAM_ABBREVIATION", "PTS"]
    home = (df[df["HOME_FLAG"]][keep]
            .rename(columns={"TEAM_ID": "HOME_TEAM_ID",
                             "TEAM_ABBREVIATION": "HOME_TEAM",
                             "PTS": "HOME_PTS"}))
    away = (df[~df["HOME_FLAG"]][["GAME_ID", "TEAM_ID", "TEAM_ABBREVIATION", "PTS"]]
            .rename(columns={"TEAM_ID": "AWAY_TEAM_ID",
                             "TEAM_ABBREVIATION": "AWAY_TEAM",
                             "PTS": "AWAY_PTS"}))

    games = home.merge(away, on="GAME_ID", how="inner")

    # sanity: exactly one home + one away row per game
    assert len(games) == df["GAME_ID"].nunique(), "home/away pivot lost or duplicated games"
    print(f"Neutral-site games flagged: {games.NEUTRAL.sum()}")

    games["HOME_WIN"] = (games["HOME_PTS"] > games["AWAY_PTS"]).astype(int)
    games["MARGIN"] = games["HOME_PTS"] - games["AWAY_PTS"]        # home minus away
    games["TOTAL"] = games["HOME_PTS"] + games["AWAY_PTS"]
    games["GAME_DATE"] = pd.to_datetime(games["GAME_DATE"])

    games = games.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)

    cols = ["GAME_ID", "GAME_DATE", "SEASON", "SEASON_TYPE", "NEUTRAL",
            "HOME_TEAM_ID", "HOME_TEAM", "AWAY_TEAM_ID", "AWAY_TEAM",
            "HOME_PTS", "AWAY_PTS", "MARGIN", "TOTAL", "HOME_WIN"]
    games = games[cols]
    games.to_parquet(OUT, index=False)

    print(f"Wrote {len(games):,} games to {OUT}")
    print(f"Date range: {games.GAME_DATE.min().date()} → {games.GAME_DATE.max().date()}")
    print(f"Seasons: {games.SEASON.nunique()} | ties (should be 0): {(games.MARGIN == 0).sum()}")
    print(f"Home win rate: {games.HOME_WIN.mean():.3f}")
    print("\nPreview:")
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(games.head())


if __name__ == "__main__":
    main()
