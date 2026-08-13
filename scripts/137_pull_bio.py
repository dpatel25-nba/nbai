"""
Pull player biographical data (commonplayerinfo) — height, weight, birthdate.

Two long-standing gaps close with this one endpoint:

  HEIGHT AND WEIGHT. The assignment model (script 122) resolves matchups with
  G/F/C buckets only, so every guard is interchangeable with every other guard.
  Your north-star notes list height explicitly as needed for "shooting vs
  taller/shorter defenders", and it is the missing input for real mismatch
  modelling rather than positional approximation.

  BIRTHDATE. The projection engine (script 23) is age-blind and the notes call
  aging curves "the biggest remaining lift" for offseason projection. Birthdates
  are the blocker, and they are here.

Same discipline as the other scrapers: raw JSON to disk before parsing, fully
resumable (existing files are skipped with no request), polite spacing, retry
with exponential backoff, failures logged and the run continues.

Height arrives as "6-11" and is parsed to inches. Players are pulled newest-first
so the most relevant seasons are covered even if the run is interrupted.

Usage:
  python scripts/137_pull_bio.py                 # everyone since 2022-23
  python scripts/137_pull_bio.py --since 2013-14 # the whole archive
"""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import commonplayerinfo

ROOT = Path(__file__).resolve().parents[1]
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
RAW = ROOT / "data" / "raw" / "playerinfo"
OUT = ROOT / "data" / "parquet" / "player_bio.parquet"
LOG = ROOT / "data" / "raw" / "bio_pull.log"

SLEEP = 1.6
MAX_RETRIES = 4
BACKOFF = 2.0


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def height_inches(h) -> float | None:
    """'6-11' -> 83.0"""
    if not isinstance(h, str) or "-" not in h:
        return None
    try:
        ft, inch = h.split("-")
        return int(ft) * 12 + int(inch)
    except ValueError:
        return None


def fetch(pid: int) -> str:
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f"{pid}.json"
    if out.exists():
        return "skip"
    for attempt in range(MAX_RETRIES + 1):
        try:
            d = commonplayerinfo.CommonPlayerInfo(player_id=pid, timeout=45).get_dict()
            out.write_text(json.dumps(d))
            time.sleep(SLEEP)
            return "ok"
        except Exception as exc:  # noqa: BLE001 — never kill the run
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF * (2 ** attempt) + random.uniform(0, 1))
            else:
                log(f"FAIL {pid}: {type(exc).__name__} {str(exc)[:80]}")
                return "fail"
    return "fail"


def build() -> None:
    rows = []
    for f in sorted(RAW.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        rs = next((r for r in d.get("resultSets", [])
                   if r.get("name") == "CommonPlayerInfo"), None)
        if not rs or not rs.get("rowSet"):
            continue
        rec = dict(zip(rs["headers"], rs["rowSet"][0]))
        rows.append({
            "PLAYER_ID": rec.get("PERSON_ID"),
            "PLAYER": f"{rec.get('FIRST_NAME','')} {rec.get('LAST_NAME','')}".strip(),
            "HEIGHT_IN": height_inches(rec.get("HEIGHT")),
            "WEIGHT_LB": pd.to_numeric(rec.get("WEIGHT"), errors="coerce"),
            "BIRTHDATE": rec.get("BIRTHDATE"),
            "POSITION": rec.get("POSITION"),
            "DRAFT_YEAR": rec.get("DRAFT_YEAR"),
            "DRAFT_NUMBER": rec.get("DRAFT_NUMBER"),
            "FROM_YEAR": rec.get("FROM_YEAR"),
        })
    df = pd.DataFrame(rows)
    if not len(df):
        log("no bio rows parsed yet")
        return
    df["BIRTHDATE"] = pd.to_datetime(df.BIRTHDATE, errors="coerce", utc=True).dt.tz_localize(None)
    df.to_parquet(OUT, index=False)
    log(f"player_bio: {len(df):,} players  "
        f"height {df.HEIGHT_IN.notna().mean()*100:.0f}%  "
        f"birthdate {df.BIRTHDATE.notna().mean()*100:.0f}%")
    q = df.dropna(subset=["HEIGHT_IN"])
    if len(q) > 20:
        log(f"  height range {q.HEIGHT_IN.min():.0f}-{q.HEIGHT_IN.max():.0f} in, "
            f"mean {q.HEIGHT_IN.mean():.1f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2022-23")
    ap.add_argument("--build-only", action="store_true")
    args = ap.parse_args()

    if args.build_only:
        build()
        return

    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "MIN"])
    ps = ps[ps.SEASON >= args.since]
    # newest and highest-minute players first, so an interrupted run still covers
    # the players that matter most
    order = (ps.sort_values(["SEASON", "MIN"], ascending=[False, False])
               .drop_duplicates("PLAYER_ID").PLAYER_ID.astype(int).tolist())
    log(f"===== BIO PULL — {len(order):,} players since {args.since} =====")
    ok = skip = fail = 0
    t0 = time.time()
    for i, pid in enumerate(order, 1):
        r = fetch(pid)
        ok += r == "ok"; skip += r == "skip"; fail += r == "fail"
        if i % 50 == 0:
            mins = (time.time() - t0) / 60
            eta = mins / max(ok, 1) * max(len(order) - i, 0) / 60
            log(f"  [{i}/{len(order)}] ok={ok} skip={skip} fail={fail} "
                f"| {mins:.1f} min | ETA {eta:.1f} h")
    log(f"===== DONE ok={ok} skip={skip} fail={fail} =====")
    build()


if __name__ == "__main__":
    main()
