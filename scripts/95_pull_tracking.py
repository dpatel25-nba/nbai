"""
Overnight pull — advanced tracking / play-type / hustle data (new data layer).

Everything so far derives from box scores. This adds genuinely orthogonal data
serving both north-stars (player props + the matchup simulator):

  PHASE A (season-level, fast — a few calls per season):
    - leaguedashptstats : shot-diet & tracking splits (drives, catch&shoot vs
        pull-up, touches by location, passing, rebounding chances) — 2013-14+
    - synergyplaytypes  : efficiency BY PLAY TYPE (iso, P&R, spot-up, post,
        transition…), offense AND defense — the simulator's raw material — 2015-16+
    - leaguedashptdefend: opponent FG% defended by shot zone — the defensive gap — 2013-14+

  PHASE B (per-game, the overnight grind):
    - hustlestatsboxscore: deflections, contested shots, screen assists, box-outs,
        charges, loose balls — effort/defense box scores never capture — 2015-16+

Resumable (skips existing files), polite (~1.3s), retry w/ backoff. Raw JSON only —
parsing into parquet happens later, once we see what's there.

Usage: python scripts/95_pull_tracking.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import (hustlestatsboxscore, leaguedashptdefend,
                                     leaguedashptstats, synergyplaytypes)

ROOT = Path(__file__).resolve().parents[1]
GAMES = ROOT / "data" / "parquet" / "games.parquet"
RAW = ROOT / "data" / "raw"
LOG = RAW / "tracking_pull.log"

SEASONS = [f"{y}-{str(y + 1)[2:]}" for y in range(2013, 2026)]        # 2013-14 … 2025-26
SYNERGY_SEASONS = [s for s in SEASONS if s >= "2015-16"]
HUSTLE_MIN_SEASON = "2015-16"

PT_MEASURES = ["Drives", "CatchShoot", "PullUpShot", "Passing", "Possessions",
               "Rebounding", "Defense", "SpeedDistance", "ElbowTouch", "PostTouch",
               "PaintTouch"]
SYN_TYPES = ["Transition", "Isolation", "PRBallHandler", "PRRollman", "Postup",
             "Spotup", "Handoff", "Cut", "OffScreen", "OffRebound", "Misc"]
DEF_CATS = ["Overall", "3 Pointers", "2 Pointers", "Less Than 6Ft",
            "Less Than 10Ft", "Greater Than 15Ft"]

MAX_RETRIES = 4
SLEEP = 1.3


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')}  {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def fetch(path: Path, make):
    """Fetch one endpoint to raw JSON, resumable + retried. Returns 'skip'/'ok'/'fail'."""
    if path.exists():
        return "skip"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            d = make().get_dict()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(d))
            time.sleep(SLEEP)
            return "ok"
        except Exception as e:
            wait = SLEEP * 2 ** attempt
            if attempt == MAX_RETRIES:
                log(f"    FAIL {path.name}: {type(e).__name__} {str(e)[:100]}")
                return "fail"
            time.sleep(wait)
    return "fail"


def phase_a() -> None:
    log("===== PHASE A — season-level tracking / synergy / defense =====")
    ok = skip = fail = 0
    # tracking splits (Player + Team)
    for season in SEASONS:
        for pt in PT_MEASURES:
            for pot in ("Player", "Team"):
                p = RAW / "ptstats" / f"{season}_{pt}_{pot}.json"
                r = fetch(p, lambda s=season, m=pt, o=pot: leaguedashptstats.LeagueDashPtStats(
                    season=s, pt_measure_type=m, player_or_team=o,
                    per_mode_simple="PerGame", season_type_all_star="Regular Season", timeout=45))
                ok += r == "ok"; skip += r == "skip"; fail += r == "fail"
        log(f"  ptstats {season} done  (ok={ok} skip={skip} fail={fail})")
    # synergy play types (offense + defense, Player + Team)
    for season in SYNERGY_SEASONS:
        for pt in SYN_TYPES:
            for grp in ("offensive", "defensive"):
                for pot in ("P", "T"):
                    p = RAW / "synergy" / f"{season}_{pt}_{grp}_{pot}.json"
                    r = fetch(p, lambda s=season, m=pt, g=grp, o=pot: synergyplaytypes.SynergyPlayTypes(
                        season=s, play_type_nullable=m, type_grouping_nullable=g,
                        player_or_team_abbreviation=o, season_type_all_star="Regular Season",
                        per_mode_simple="PerGame", timeout=45))
                    ok += r == "ok"; skip += r == "skip"; fail += r == "fail"
        log(f"  synergy {season} done  (ok={ok} skip={skip} fail={fail})")
    # defensive tracking by zone
    for season in SEASONS:
        for cat in DEF_CATS:
            p = RAW / "ptdefend" / f"{season}_{cat.replace(' ', '')}.json"
            r = fetch(p, lambda s=season, c=cat: leaguedashptdefend.LeagueDashPtDefend(
                season=s, defense_category=c, per_mode_simple="PerGame",
                season_type_all_star="Regular Season", timeout=45))
            ok += r == "ok"; skip += r == "skip"; fail += r == "fail"
        log(f"  ptdefend {season} done  (ok={ok} skip={skip} fail={fail})")
    log(f"PHASE A complete — ok={ok} skip={skip} fail={fail}")


def phase_b() -> None:
    log("===== PHASE B — per-game hustle stats (2015-16+) =====")
    g = pd.read_parquet(GAMES, columns=["GAME_ID", "SEASON"])
    gids = sorted(g[g.SEASON >= HUSTLE_MIN_SEASON].GAME_ID.unique())
    log(f"  {len(gids):,} games to pull")
    ok = skip = fail = 0
    t0 = time.time()
    for i, gid in enumerate(gids, 1):
        p = RAW / "hustle" / f"{gid}.json"
        r = fetch(p, lambda x=gid: hustlestatsboxscore.HustleStatsBoxScore(game_id=x, timeout=45))
        ok += r == "ok"; skip += r == "skip"; fail += r == "fail"
        if i % 100 == 0:
            mins = (time.time() - t0) / 60
            eta = mins / i * (len(gids) - i) / 60
            log(f"  [{i:,}/{len(gids):,}] ok={ok} skip={skip} fail={fail} | {mins:.1f} min | ETA {eta:.1f} h")
    log(f"PHASE B complete — ok={ok} skip={skip} fail={fail}")


def main() -> None:
    log("========== TRACKING PULL START ==========")
    phase_a()
    phase_b()
    log("========== TRACKING PULL COMPLETE ==========")


if __name__ == "__main__":
    main()
