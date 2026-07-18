"""
Scrape per-game raw JSON for every GAME_ID in game_logs.parquet.

For each game we fetch four v3 endpoints and save each response as its own
raw JSON file. This is the long-running job (~16.7k games x 4 endpoints).
It is fully resumable: any file that already exists on disk is skipped with
no API request, so you can stop and restart (or rerun after failures) freely.

Concurrency: --workers N processes N games in parallel (each game's 4 endpoints
run sequentially inside its worker). N=2 roughly halves wall-clock vs single
threaded. Each request still sleeps ~1.7s and retries with exponential backoff,
so transient throttling from the (undocumented) NBA API is self-healing.

Endpoints:
  boxscoretraditionalv3   -> traditional player/team box score
  boxscoreadvancedv3      -> advanced player/team box score
  playbyplayv3            -> play-by-play actions
  boxscoreplayertrackv3   -> player tracking box score

Pipeline rules honored here:
  1. Save raw JSON to disk BEFORE any parsing (parsing happens in a later step).
  2. Resumable: skip files that already exist.
  3. Polite: sleep ~1.7s between requests; retry w/ backoff; log failures & keep going.

Usage:
  python scripts/02_scrape_boxscores.py --season 2013-14 --workers 2
  python scripts/02_scrape_boxscores.py --limit 5 --workers 2   # test
  python scripts/02_scrape_boxscores.py                         # full run, 1 worker
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import (
    boxscoreadvancedv3,
    boxscoreplayertrackv3,
    boxscoretraditionalv3,
    playbyplayv3,
)

# --- paths ---
ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
GAME_LOGS = ROOT / "data" / "parquet" / "game_logs.parquet"
LOG_FILE = RAW_DIR / "failures.log"

SLEEP_SECONDS = 1.7        # polite delay after each successful/attempted request
REQUEST_TIMEOUT = 60       # per-request network timeout
MAX_RETRIES = 4            # retries per request before giving up (then logged)
BACKOFF_BASE = 2.0         # backoff = BACKOFF_BASE * 2**attempt (+ jitter)

# name -> callable(game_id) that returns the endpoint object
ENDPOINTS = {
    "boxscoretraditionalv3": lambda gid: boxscoretraditionalv3.BoxScoreTraditionalV3(
        game_id=gid, timeout=REQUEST_TIMEOUT
    ),
    "boxscoreadvancedv3": lambda gid: boxscoreadvancedv3.BoxScoreAdvancedV3(
        game_id=gid, timeout=REQUEST_TIMEOUT
    ),
    "playbyplayv3": lambda gid: playbyplayv3.PlayByPlayV3(
        game_id=gid, timeout=REQUEST_TIMEOUT
    ),
    "boxscoreplayertrackv3": lambda gid: boxscoreplayertrackv3.BoxScorePlayerTrackV3(
        game_id=gid, timeout=REQUEST_TIMEOUT
    ),
}

_log_lock = threading.Lock()
_print_lock = threading.Lock()


def log_failure(message: str) -> None:
    stamped = f"{datetime.now().isoformat(timespec='seconds')}  {message}"
    with _log_lock:
        with open(LOG_FILE, "a") as f:
            f.write(stamped + "\n")
    with _print_lock:
        print("  ! " + message)


def load_game_ids(season: str | None) -> list[str]:
    df = pd.read_parquet(GAME_LOGS, columns=["GAME_ID", "GAME_DATE", "SEASON"])
    if season:
        df = df[df["SEASON"] == season]
    # unique games in chronological order (deduped from the 2-rows-per-game logs)
    df = df.drop_duplicates("GAME_ID").sort_values("GAME_DATE")
    return df["GAME_ID"].tolist()


def fetch_one(endpoint_name: str, game_id: str) -> str:
    """Fetch one endpoint for one game with retry + exponential backoff.

    Returns: "skip" (already on disk), "ok" (saved), or "fail" (logged & given up).
    """
    out_dir = RAW_DIR / endpoint_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{game_id}.json"

    if out_path.exists():
        return "skip"

    for attempt in range(MAX_RETRIES + 1):
        try:
            payload = ENDPOINTS[endpoint_name](game_id).get_dict()
            with open(out_path, "w") as f:
                json.dump(payload, f)
            time.sleep(SLEEP_SECONDS)  # polite spacing after a real request
            return "ok"
        except Exception as exc:  # noqa: BLE001 - never crash the run
            if attempt < MAX_RETRIES:
                # exponential backoff with jitter so throttling self-heals
                backoff = BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1)
                time.sleep(backoff)
            else:
                log_failure(f"FETCH FAIL {endpoint_name} {game_id}: {exc!r}")
                return "fail"
    return "fail"


def process_game(game_id: str, endpoints: list[str]) -> dict[str, int]:
    """Fetch all requested endpoints for a single game. Runs inside a worker."""
    tally = {"ok": 0, "skip": 0, "fail": 0}
    for endpoint_name in endpoints:
        tally[fetch_one(endpoint_name, game_id)] += 1
    return tally


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="only process the first N games")
    parser.add_argument("--season", type=str, default=None,
                        help="restrict to one season, e.g. 2024-25")
    parser.add_argument("--endpoints", nargs="+", default=list(ENDPOINTS),
                        choices=list(ENDPOINTS),
                        help="subset of endpoints to fetch")
    parser.add_argument("--workers", type=int, default=1,
                        help="games processed in parallel (2 recommended max)")
    args = parser.parse_args()

    game_ids = load_game_ids(args.season)
    if args.limit:
        game_ids = game_ids[: args.limit]

    total = len(game_ids)
    print(f"Games to process: {total:,}")
    print(f"Endpoints: {', '.join(args.endpoints)}")
    print(f"Workers: {args.workers}")
    print(f"Raw output: {RAW_DIR}\n")

    totals = {"ok": 0, "skip": 0, "fail": 0}
    done = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_game, gid, args.endpoints): gid
            for gid in game_ids
        }
        for fut in as_completed(futures):
            tally = fut.result()
            for k in totals:
                totals[k] += tally[k]
            done += 1
            if done % 25 == 0 or done == total:
                elapsed = time.time() - start
                rate = elapsed / done
                eta = (total - done) * rate / 3600
                with _print_lock:
                    print(f"[{done:,}/{total:,}] games | "
                          f"ok={totals['ok']:,} skip={totals['skip']:,} "
                          f"fail={totals['fail']:,} | "
                          f"{elapsed/60:.1f} min | ETA {eta:.1f} h")

    print("\n=== DONE ===")
    print(f"Games: {total:,} | new saved: {totals['ok']:,} | "
          f"skipped: {totals['skip']:,} | failed: {totals['fail']:,}")
    if totals["fail"]:
        print(f"Re-run the same command to retry the {totals['fail']:,} failures "
              f"(see {LOG_FILE}).")
    else:
        print("No failures. Re-run any time; existing files are skipped.")


if __name__ == "__main__":
    main()
