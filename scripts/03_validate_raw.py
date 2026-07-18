"""
Validate the raw per-game JSON for a season for completeness and internal /
cross-source consistency. Catches missing files, missing numbers, and games
whose stats don't add up — before we trust the data or scrape more seasons.

Checks per game:
  1. COMPLETENESS: all 4 endpoint files exist for every game_id in the season.
  2. TRADITIONAL internal: sum(player points) == team total points, each team.
  3. CROSS-SOURCE: traditional team points == game_logs PTS for that game+team.
  4. PBP consistency: final play-by-play score == traditional team points.
  5. MISSING NUMBERS: team-level points/rebounds/assists are non-null; every
     player who logged minutes has a non-null points value.

Legitimate nulls (FG% with 0 attempts, DNP players' empty stat lines) are NOT
flagged — we only flag numbers that should exist but don't, or that disagree.

Usage: python scripts/03_validate_raw.py --season 2013-14
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
GAME_LOGS = ROOT / "data" / "parquet" / "game_logs.parquet"

ENDPOINTS = ["boxscoretraditionalv3", "boxscoreadvancedv3",
             "playbyplayv3", "boxscoreplayertrackv3"]


def num(v) -> float:
    """None/'' -> 0.0, else float. Used only where a real number is expected."""
    if v is None or v == "":
        return 0.0
    return float(v)


def minutes_played(stats: dict) -> bool:
    """True if the player actually logged court time (v3 minutes look like 'PT34M...' or '34:00')."""
    m = stats.get("minutes")
    if not m:
        return False
    s = str(m)
    # zero-minute encodings seen in v3: 'PT00M00.00S', '0:00', '00:00'
    return not (s.replace("PT", "").replace("S", "").replace("M", ":")
                .startswith(("00:00", "0:00")) or s in ("PT00M00.00S", ""))


def load_game(endpoint: str, gid: str, key: str):
    path = RAW / endpoint / f"{gid}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)[key]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", required=True)
    args = ap.parse_args()

    logs = pd.read_parquet(GAME_LOGS, columns=["GAME_ID", "TEAM_ID", "PTS", "SEASON"])
    logs = logs[logs["SEASON"] == args.season]
    game_ids = sorted(logs["GAME_ID"].unique())
    # (game_id, team_id) -> PTS from the master game logs
    logs_pts = {(r.GAME_ID, r.TEAM_ID): r.PTS for r in logs.itertuples()}

    print(f"Season {args.season}: {len(game_ids):,} games\n")

    # --- Check 1: completeness ---
    missing_files = []
    for gid in game_ids:
        for ep in ENDPOINTS:
            if not (RAW / ep / f"{gid}.json").exists():
                missing_files.append((gid, ep))

    fails = {"trad_sum": [], "cross_source": [], "pbp": [],
             "null_team": [], "null_player": []}
    checked = 0

    for gid in game_ids:
        trad = load_game("boxscoretraditionalv3", gid, "boxScoreTraditional")
        if trad is None:
            continue
        checked += 1
        pbp = load_game("playbyplayv3", gid, "game")

        for side in ("homeTeam", "awayTeam"):
            team = trad[side]
            tstats = team["statistics"]
            team_id = team["teamId"]
            team_pts = tstats.get("points")

            # Check 5a: team-level core numbers present
            for fld in ("points", "reboundsTotal", "assists"):
                if tstats.get(fld) is None:
                    fails["null_team"].append((gid, side, fld))

            # Check 2: player points sum to team points
            psum = sum(num(p["statistics"].get("points")) for p in team["players"])
            if team_pts is not None and abs(psum - num(team_pts)) > 0.5:
                fails["trad_sum"].append((gid, side, psum, team_pts))

            # Check 5b: any player with minutes but null points
            for p in team["players"]:
                st = p["statistics"]
                if minutes_played(st) and st.get("points") is None:
                    fails["null_player"].append((gid, p.get("personId")))

            # Check 3: cross-source vs game_logs
            gl = logs_pts.get((gid, team_id))
            if gl is not None and team_pts is not None and int(num(team_pts)) != int(gl):
                fails["cross_source"].append((gid, team_id, team_pts, gl))

        # Check 4: PBP final score == traditional points
        if pbp is not None:
            hp = num(trad["homeTeam"]["statistics"].get("points"))
            ap_ = num(trad["awayTeam"]["statistics"].get("points"))
            # cumulative score is monotonic, so the final = max seen. Using max
            # (not the last row) ignores stale scores on period/end marker rows.
            scores_h = [num(a.get("scoreHome")) for a in pbp["actions"]
                        if a.get("scoreHome") not in (None, "")]
            scores_a = [num(a.get("scoreAway")) for a in pbp["actions"]
                        if a.get("scoreAway") not in (None, "")]
            if scores_h:
                fh, fa = max(scores_h), max(scores_a)
                if fh != hp or fa != ap_:
                    fails["pbp"].append((gid, (fh, fa), (hp, ap_)))

    # --- report ---
    def show(name, items, fmt):
        status = "OK" if not items else f"{len(items)} FAIL"
        print(f"[{status:>8}] {name}")
        for it in items[:8]:
            print("           ", fmt(it))
        if len(items) > 8:
            print(f"            ... +{len(items)-8} more")

    print(f"Games with all 4 files parsed: {checked:,}/{len(game_ids):,}\n")
    show("1. completeness (missing files)", missing_files, lambda x: x)
    show("2. player points sum == team points", fails["trad_sum"],
         lambda x: f"{x[0]} {x[1]}: players={x[2]:.0f} team={x[3]}")
    show("3. team points == game_logs PTS", fails["cross_source"],
         lambda x: f"{x[0]} team {x[1]}: box={x[2]} logs={x[3]}")
    show("4. pbp final score == box score", fails["pbp"],
         lambda x: f"{x[0]}: pbp={x[1]} box={x[2]}")
    show("5a. team core stats non-null", fails["null_team"],
         lambda x: f"{x[0]} {x[1]}.{x[2]} is null")
    show("5b. played players have points", fails["null_player"],
         lambda x: f"{x[0]} player {x[1]} null pts w/ minutes")

    total_fail = len(missing_files) + sum(len(v) for v in fails.values())
    print(f"\n{'='*50}")
    print("ALL CHECKS PASSED — data is complete & consistent." if total_fail == 0
          else f"{total_fail} issue(s) found — see above.")


if __name__ == "__main__":
    main()
