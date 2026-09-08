"""
Pull CURRENT team rosters — the simulator has been reading them off box scores.

`154_matchup.py` derives a roster from who actually played for a team in its most
recent games. That is a good way to learn a rotation and a bad way to learn a
roster, and the difference only shows up in the offseason: our game data ends
2026-06-13, so every trade, signing and draft pick since then is invisible.
Jaylen Brown still appeared on Boston because the last game he played was in a
Boston uniform.

It also means a drafted rookie CANNOT be rostered. Script 141 built draft-slot
priors so the engine can give a player with no NBA history a plausible rate
profile, and script 124 wires them in — but a rookie who has never played has no
row in `player_games`, so the roster builder never proposes him and the priors
sit unused for exactly the players they were built for.

This pulls the real roster from the league's own endpoint, which also carries
two things worth having:

  EXP           "R" marks a rookie, so the engine knows to use the draft-slot
                prior rather than a replacement-level profile.
  HOW_ACQUIRED  "#40 Pick in 2026 Draft" gives the draft slot for players who
                are not in our bio archive yet, which is every 2026 draftee.

Output: data/parquet/team_rosters.parquet
Usage: python scripts/156_pull_rosters.py [--season 2026-27]
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT = ROOT / "data" / "parquet" / "team_rosters.parquet"

PICK = re.compile(r"#(\d+)\s+Pick", re.I)
PAUSE = 0.7          # the endpoint rate-limits well before this


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2026-27")
    ap.add_argument("--pause", type=float, default=PAUSE)
    args = ap.parse_args()

    from nba_api.stats.endpoints import commonteamroster

    g = pd.read_parquet(GAMES, columns=["SEASON", "HOME_TEAM_ID", "HOME_TEAM"])
    latest = sorted(g.SEASON.unique())[-1]
    teams = (g[g.SEASON == latest][["HOME_TEAM_ID", "HOME_TEAM"]]
             .drop_duplicates().sort_values("HOME_TEAM"))
    print(f"pulling {args.season} rosters for {len(teams)} teams")

    rows, failed = [], []
    for r in teams.itertuples():
        tid, abbr = int(r.HOME_TEAM_ID), r.HOME_TEAM
        try:
            d = commonteamroster.CommonTeamRoster(
                team_id=tid, season=args.season, timeout=30
            ).get_dict()["resultSets"][0]
        except Exception as e:                      # network, rate limit, timeout
            failed.append(abbr)
            print(f"  {abbr}: FAILED ({type(e).__name__})", flush=True)
            time.sleep(args.pause * 3)
            continue
        head = {h: i for i, h in enumerate(d["headers"])}
        n_rk = 0
        for row in d["rowSet"]:
            got = str(row[head["HOW_ACQUIRED"]] or "")
            m = PICK.search(got)
            exp = str(row[head["EXP"]] or "")
            rookie = exp.upper() == "R"
            n_rk += rookie
            rows.append({
                "SEASON": args.season, "TEAM_ID": tid, "TEAM": abbr,
                "PLAYER_ID": int(row[head["PLAYER_ID"]]),
                "PLAYER": row[head["PLAYER"]],
                "POSITION": row[head["POSITION"]],
                "HEIGHT": row[head["HEIGHT"]], "WEIGHT": row[head["WEIGHT"]],
                "AGE": row[head["AGE"]], "EXP": exp,
                "IS_ROOKIE": bool(rookie),
                "DRAFT_SLOT": int(m.group(1)) if m else (0 if "undraft" in got.lower()
                                                         else None),
                "HOW_ACQUIRED": got})
        print(f"  {abbr}: {len(d['rowSet'])} players, {n_rk} rookies", flush=True)
        time.sleep(args.pause)

    if not rows:
        raise SystemExit("no rosters pulled — refusing to write an empty file")
    df = pd.DataFrame(rows)
    if failed:
        # a partial pull would silently shrink some team to a handful of players,
        # and the 240-minute rescale would then hand those few enormous minutes
        raise SystemExit(f"teams failed: {', '.join(failed)} — rerun rather than "
                         f"write a partial roster file")
    df.to_parquet(OUT, index=False)
    print(f"\nWrote {OUT}: {len(df)} players, {df.TEAM_ID.nunique()} teams, "
          f"{int(df.IS_ROOKIE.sum())} rookies")
    print(f"  draft slot known for {int(df.DRAFT_SLOT.notna().sum())} of them")


if __name__ == "__main__":
    main()
