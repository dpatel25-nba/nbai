"""
Pull NBA game logs for all seasons 2013-14 through 2025-26.

For each season we fetch both Regular Season and Playoffs game logs at the TEAM
level via the leaguegamelog endpoint. This is the master list of GAME_IDs that
every downstream scraper hangs off of.

Pipeline rules honored here:
  1. Save raw JSON to disk BEFORE parsing, so parsing bugs never require re-scraping.
  2. Resumable: skip raw files that already exist.
  3. Polite to the API: sleep ~1.5-2s between requests, log failures and keep going.
  4. Parquet keeps GAME_ID / TEAM_ID join keys, and GAME_DATE as a real datetime.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import leaguegamelog

# --- paths ---
ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "leaguegamelog"
PARQUET_DIR = ROOT / "data" / "parquet"
LOG_FILE = ROOT / "data" / "raw" / "failures.log"

RAW_DIR.mkdir(parents=True, exist_ok=True)
PARQUET_DIR.mkdir(parents=True, exist_ok=True)

# --- what to pull ---
# Seasons 2013-14 .. 2025-26 (season tracking data begins 2013-14).
SEASONS = [f"{y}-{str(y + 1)[-2:]}" for y in range(2013, 2026)]
SEASON_TYPES = ["Regular Season", "Playoffs"]

SLEEP_SECONDS = 1.7
REQUEST_TIMEOUT = 60


def log_failure(message: str) -> None:
    with open(LOG_FILE, "a") as f:
        f.write(message + "\n")
    print("  ! " + message)


def fetch_raw(season: str, season_type: str) -> dict | None:
    """Fetch one (season, season_type) game log, saving raw JSON before parsing.

    Returns the parsed JSON dict, or None on failure (already logged).
    Resumable: if the raw file exists we load it from disk instead of re-fetching.
    """
    slug = season_type.replace(" ", "")
    raw_path = RAW_DIR / f"{season}_{slug}.json"

    if raw_path.exists():
        print(f"  · cached  {raw_path.name}")
        with open(raw_path) as f:
            return json.load(f)

    try:
        resp = leaguegamelog.LeagueGameLog(
            season=season,
            season_type_all_star=season_type,
            player_or_team_abbreviation="T",  # team-level rows
            timeout=REQUEST_TIMEOUT,
        )
        payload = resp.get_dict()
    except Exception as exc:  # noqa: BLE001 - keep going on any API error
        log_failure(f"FETCH FAIL {season} {season_type}: {exc!r}")
        return None

    # Save raw JSON to disk BEFORE parsing.
    with open(raw_path, "w") as f:
        json.dump(payload, f)
    print(f"  ✓ saved   {raw_path.name}")

    time.sleep(SLEEP_SECONDS)
    return payload


def parse(payload: dict, season: str, season_type: str) -> pd.DataFrame:
    """Turn a leaguegamelog resultSet into a tidy DataFrame."""
    result = payload["resultSets"][0]
    df = pd.DataFrame(result["rowSet"], columns=result["headers"])
    df["SEASON"] = season
    df["SEASON_TYPE"] = season_type
    return df


def main() -> None:
    frames = []
    for season in SEASONS:
        for season_type in SEASON_TYPES:
            print(f"{season} / {season_type}")
            payload = fetch_raw(season, season_type)
            if payload is None:
                continue
            try:
                frames.append(parse(payload, season, season_type))
            except Exception as exc:  # noqa: BLE001
                log_failure(f"PARSE FAIL {season} {season_type}: {exc!r}")

    if not frames:
        print("No data collected — nothing to write.")
        return

    all_logs = pd.concat(frames, ignore_index=True)

    # GAME_DATE as a real datetime; join keys stay as-is.
    all_logs["GAME_DATE"] = pd.to_datetime(all_logs["GAME_DATE"])

    out_path = PARQUET_DIR / "game_logs.parquet"
    all_logs.to_parquet(out_path, index=False)

    print("\n=== DONE ===")
    print(f"Wrote {len(all_logs):,} rows to {out_path}")
    print(f"Seasons: {all_logs['SEASON'].nunique()} | "
          f"Unique games: {all_logs['GAME_ID'].nunique():,}")
    print("\nColumns:", list(all_logs.columns))
    print("\nPreview:")
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(all_logs.head())


if __name__ == "__main__":
    main()
