"""
Unattended driver: scrape + validate every remaining season, one after another.

For each season 2014-15 .. 2025-26 it:
  1. runs the scraper (2 workers, resumable) — retrying up to 3x if the process
     itself exits non-zero, so a one-off crash doesn't stall the night;
  2. runs the validator and records the full check table.

Everything is resumable, so if this is interrupted just launch it again — it
skips games already on disk and re-validates. Progress is written to
data/raw/overnight.log (and stdout).

Usage: python scripts/run_overnight.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
SCRAPER = ROOT / "scripts" / "02_scrape_boxscores.py"
VALIDATOR = ROOT / "scripts" / "03_validate_raw.py"
LOG = ROOT / "data" / "raw" / "overnight.log"

# 2013-14 already done & validated; do the remaining 12 seasons.
SEASONS = [f"{y}-{str(y + 1)[-2:]}" for y in range(2014, 2026)]


def logline(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')}  {msg}"
    with open(LOG, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def run_scrape(season: str) -> bool:
    for attempt in range(1, 4):
        rc = subprocess.run(
            [PY, str(SCRAPER), "--season", season, "--workers", "2"]
        ).returncode
        if rc == 0:
            logline(f"scrape {season}: OK (attempt {attempt})")
            return True
        logline(f"scrape {season}: exit {rc} (attempt {attempt}) — retry in 30s")
        time.sleep(30)
    logline(f"scrape {season}: FAILED after 3 attempts — moving on (resumable later)")
    return False


def run_validate(season: str) -> None:
    out = subprocess.run(
        [PY, str(VALIDATOR), "--season", season],
        capture_output=True, text=True,
    )
    for ln in out.stdout.splitlines():
        if ln.strip():
            logline(f"  [validate {season}] {ln}")


def main() -> None:
    logline(f"===== OVERNIGHT RUN START — {len(SEASONS)} seasons =====")
    start = time.time()
    for i, season in enumerate(SEASONS, 1):
        logline(f"===== [{i}/{len(SEASONS)}] {season} — scrape =====")
        run_scrape(season)
        logline(f"----- [{i}/{len(SEASONS)}] {season} — validate -----")
        run_validate(season)
        logline(f"===== {season} done | {(time.time()-start)/3600:.1f}h elapsed =====")
    logline("===== OVERNIGHT RUN COMPLETE — all seasons attempted =====")


if __name__ == "__main__":
    main()
