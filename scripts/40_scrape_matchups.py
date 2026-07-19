"""
Scrape boxscorematchupsv3 (who-guarded-whom) for every GAME_ID from 2017-18 on.

Matchup tracking begins 2017-18, so we only pull those seasons. Same resumable,
polite, retry-with-backoff pattern as the box-score scraper. Raw JSON per game
to data/raw/boxscorematchupsv3/<game_id>.json.

Usage:
  python scripts/40_scrape_matchups.py --workers 2
  python scripts/40_scrape_matchups.py --limit 3 --workers 2   # test
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
from nba_api.stats.endpoints import boxscorematchupsv3

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "boxscorematchupsv3"
GAME_LOGS = ROOT / "data" / "parquet" / "game_logs.parquet"
LOG_FILE = ROOT / "data" / "raw" / "failures.log"

SLEEP_SECONDS = 1.7
REQUEST_TIMEOUT = 60
MAX_RETRIES = 4
BACKOFF_BASE = 2.0
FIRST_SEASON = "2017-18"

_log_lock = threading.Lock()


def log_failure(message: str) -> None:
    with _log_lock:
        with open(LOG_FILE, "a") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}  {message}\n")
    print("  ! " + message, flush=True)


def load_game_ids() -> list[str]:
    df = pd.read_parquet(GAME_LOGS, columns=["GAME_ID", "GAME_DATE", "SEASON"])
    df = df[df["SEASON"] >= FIRST_SEASON].drop_duplicates("GAME_ID").sort_values("GAME_DATE")
    return df["GAME_ID"].tolist()


def fetch_one(game_id: str) -> str:
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f"{game_id}.json"
    if out.exists():
        return "skip"
    for attempt in range(MAX_RETRIES + 1):
        try:
            payload = boxscorematchupsv3.BoxScoreMatchupsV3(
                game_id=game_id, timeout=REQUEST_TIMEOUT).get_dict()
            with open(out, "w") as f:
                json.dump(payload, f)
            time.sleep(SLEEP_SECONDS)
            return "ok"
        except Exception as exc:  # noqa: BLE001
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1))
            else:
                log_failure(f"FETCH FAIL boxscorematchupsv3 {game_id}: {exc!r}")
                return "fail"
    return "fail"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    gids = load_game_ids()
    if args.limit:
        gids = gids[: args.limit]
    total = len(gids)
    print(f"Matchup games to process ({FIRST_SEASON}+): {total:,} | workers {args.workers}",
          flush=True)

    tally = {"ok": 0, "skip": 0, "fail": 0}
    done = 0
    start = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_one, g): g for g in gids}
        for fut in as_completed(futures):
            tally[fut.result()] += 1
            done += 1
            if done % 50 == 0 or done == total:
                el = time.time() - start
                eta = (total - done) * (el / done) / 3600
                print(f"[{done:,}/{total:,}] ok={tally['ok']:,} skip={tally['skip']:,} "
                      f"fail={tally['fail']:,} | {el/60:.1f} min | ETA {eta:.1f} h", flush=True)

    print(f"\nDONE — new {tally['ok']:,}, skipped {tally['skip']:,}, failed {tally['fail']:,}",
          flush=True)


if __name__ == "__main__":
    main()
