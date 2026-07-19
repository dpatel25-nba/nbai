"""
Pull referee assignments (boxscoresummaryv2 'Officials') for 2021-22 + 2022-23.

To test: do referee foul/scoring tendencies predict game totals beyond the line?
Two seasons that both have betting lines, so we can estimate each ref's tendency
and test out-of-sample. Resumable, polite. Saves raw officials JSON per game +
builds referees.parquet.

Usage: python scripts/107_pull_referees.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import boxscoresummaryv2

ROOT = Path(__file__).resolve().parents[1]
GAMES = ROOT / "data" / "parquet" / "games.parquet"
RAW = ROOT / "data" / "raw" / "refs"
OUT = ROOT / "data" / "parquet" / "referees.parquet"
LOG = ROOT / "data" / "raw" / "refs_pull.log"
SEASONS = ["2021-22", "2022-23"]
MAX_RETRIES = 4
SLEEP = 1.4


def log(m):
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def fetch(gid):
    p = RAW / f"{gid}.json"
    if p.exists():
        return "skip"
    for a in range(1, MAX_RETRIES + 1):
        try:
            d = boxscoresummaryv2.BoxScoreSummaryV2(game_id=gid, timeout=45).get_dict()
            offs = next(rs for rs in d["resultSets"] if rs["name"] == "Officials")
            rows = [dict(zip(offs["headers"], r)) for r in offs["rowSet"]]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(rows))
            time.sleep(SLEEP)
            return "ok"
        except Exception as e:
            if a == MAX_RETRIES:
                log(f"  FAIL {gid}: {type(e).__name__} {str(e)[:60]}")
                return "fail"
            time.sleep(SLEEP * 2 ** a)
    return "fail"


def build():
    rows = []
    for f in sorted(RAW.glob("*.json")):
        gid = f.stem
        for o in json.loads(f.read_text()):
            rows.append({"GAME_ID": gid, "OFFICIAL_ID": o.get("OFFICIAL_ID"),
                         "name": f"{o.get('FIRST_NAME','')} {o.get('LAST_NAME','')}".strip()})
    df = pd.DataFrame(rows)
    df.to_parquet(OUT, index=False)
    log(f"referees.parquet: {len(df):,} rows, {df.GAME_ID.nunique():,} games, "
        f"{df.OFFICIAL_ID.nunique()} unique refs")


def main():
    g = pd.read_parquet(GAMES, columns=["GAME_ID", "SEASON", "SEASON_TYPE"])
    gids = sorted(g[(g.SEASON.isin(SEASONS)) & (g.SEASON_TYPE == "Regular Season")].GAME_ID.unique())
    log(f"===== REF PULL START — {len(gids):,} games =====")
    ok = skip = fail = 0
    t0 = time.time()
    for i, gid in enumerate(gids, 1):
        r = fetch(gid)
        ok += r == "ok"; skip += r == "skip"; fail += r == "fail"
        if i % 100 == 0:
            mins = (time.time() - t0) / 60
            log(f"  [{i}/{len(gids)}] ok={ok} skip={skip} fail={fail} | {mins:.0f}m | "
                f"ETA {mins/i*(len(gids)-i):.0f}m")
    build()
    log(f"===== DONE ok={ok} skip={skip} fail={fail} =====")


if __name__ == "__main__":
    main()
