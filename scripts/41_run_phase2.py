"""
Phase-2 driver: waits for the phase-1 box-score scrape (run_overnight.py) to
finish, then keeps the machine productively pulling:
  1. backfill the 3 transient advanced-box gaps (resumable box scraper re-run),
  2. scrape boxscorematchupsv3 (who-guarded-whom, 2017-18+).

Idle-polls until phase 1 is done so the API is never hit concurrently. All
resumable — safe to re-launch. Logs to data/raw/phase2.log.

Usage: python scripts/41_run_phase2.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
LOG = ROOT / "data" / "raw" / "phase2.log"


def logline(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')}  {msg}"
    with open(LOG, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def phase1_running() -> bool:
    r = subprocess.run(["pgrep", "-f", "run_overnight.py"], capture_output=True)
    return r.returncode == 0


def main() -> None:
    logline("===== PHASE 2 QUEUED — waiting for phase-1 box-score scrape to finish =====")
    while phase1_running():
        time.sleep(60)
    logline("phase-1 finished — starting phase-2")

    logline("--- backfill: re-run box scraper to fill any transient gaps ---")
    subprocess.run([PY, str(ROOT / "scripts" / "02_scrape_boxscores.py"), "--workers", "2"])
    logline("backfill done")

    logline("--- matchups: boxscorematchupsv3 (2017-18+) ---")
    subprocess.run([PY, str(ROOT / "scripts" / "40_scrape_matchups.py"), "--workers", "2"])
    logline("matchups done")

    logline("===== PHASE 2 COMPLETE =====")


if __name__ == "__main__":
    main()
