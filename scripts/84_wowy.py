"""
WOWY (With-Or-Without-You) — a player's impact via games played vs. missed.

Possession-level on/off needs accurate stints (not available). But game-level
WOWY answers the same intuitive question — "how do the team and each teammate do
when this player is OUT (injured/rested)?" — from box scores alone:

  - team margin per game with the player IN vs OUT  -> the player's impact
  - each teammate's scoring with the player IN vs OUT -> who picks up the slack

Honest caveat: absences are small samples and confounded (injuries cluster,
schedule varies) — this is descriptive, not causal. We require enough missed
games before reporting.

Output: data/parquet/wowy.json  (per team, for the website)
Usage: python scripts/84_wowy.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GAMES = ROOT / "data" / "parquet" / "games.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
OUT = ROOT / "data" / "parquet" / "wowy.json"
SEASON = "2025-26"
MIN_OUT = 5      # min missed games to report team impact
MIN_OUT_MATE = 8  # min missed games to report teammate deltas


def main() -> None:
    g = pd.read_parquet(GAMES)
    g = g[(g.SEASON == SEASON) & (g.SEASON_TYPE == "Regular Season")]
    team_margin = {}
    team_gids = defaultdict(set)
    abbr = {}
    for r in g.itertuples():
        team_margin[(r.GAME_ID, r.HOME_TEAM_ID)] = r.MARGIN
        team_margin[(r.GAME_ID, r.AWAY_TEAM_ID)] = -r.MARGIN
        team_gids[r.HOME_TEAM_ID].add(r.GAME_ID)
        team_gids[r.AWAY_TEAM_ID].add(r.GAME_ID)
        abbr[r.HOME_TEAM_ID] = r.HOME_TEAM
        abbr[r.AWAY_TEAM_ID] = r.AWAY_TEAM

    pg = pd.read_parquet(PG, columns=["SEASON", "SEASON_TYPE", "GAME_ID", "TEAM_ID",
                                      "PLAYER_ID", "firstName", "familyName", "MIN", "points"])
    pg = pg[(pg.SEASON == SEASON) & (pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)]
    played = defaultdict(set)          # (team, pid) -> game ids
    ppts = {}                          # (team, pid, gid) -> points
    pname = {}
    roster = defaultdict(set)
    for r in pg.itertuples():
        played[(r.TEAM_ID, r.PLAYER_ID)].add(r.GAME_ID)
        ppts[(r.TEAM_ID, r.PLAYER_ID, r.GAME_ID)] = r.points
        pname[(r.TEAM_ID, r.PLAYER_ID)] = r.firstName[0] + ". " + r.familyName
        roster[r.TEAM_ID].add(r.PLAYER_ID)

    def avg(gids, tid):
        v = [team_margin[(g, tid)] for g in gids if (g, tid) in team_margin]
        return float(np.mean(v)) if v else None

    out = {}
    for tid, gids in team_gids.items():
        mates = [p for p in roster[tid] if len(played[(tid, p)]) >= 15]
        players = []
        for pid in mates:
            ing = played[(tid, pid)]
            outg = gids - ing
            if len(outg) < MIN_OUT:
                continue
            mi, mo = avg(ing, tid), avg(outg, tid)
            if mi is None or mo is None:
                continue
            rec = {"player": pname[(tid, pid)], "gp_in": len(ing), "gp_out": len(outg),
                   "margin_in": round(mi, 1), "margin_out": round(mo, 1),
                   "impact": round(mi - mo, 1), "mates": []}
            if len(outg) >= MIN_OUT_MATE:
                deltas = []
                for qid in mates:
                    if qid == pid:
                        continue
                    qin = [ppts[(tid, qid, x)] for x in ing if (tid, qid, x) in ppts]
                    qout = [ppts[(tid, qid, x)] for x in outg if (tid, qid, x) in ppts]
                    if len(qin) >= 8 and len(qout) >= 4:
                        deltas.append({"name": pname[(tid, qid)],
                                       "pin": round(np.mean(qin), 1), "pout": round(np.mean(qout), 1),
                                       "delta": round(np.mean(qout) - np.mean(qin), 1)})
                deltas.sort(key=lambda d: -abs(d["delta"]))
                rec["mates"] = deltas[:5]
            players.append(rec)
        players.sort(key=lambda p: -p["impact"])
        if players:
            out[abbr[tid]] = players

    OUT.write_text(json.dumps(out))
    print(f"WOWY computed for {len(out)} teams -> {OUT}\n")

    # face check
    for team in ["DEN", "OKC", "SAS"]:
        if team not in out:
            continue
        print(f"=== {team} — team margin swing when a player is OUT ===")
        for p in out[team][:4]:
            print(f"  {p['player']:<16} impact {p['impact']:+.1f}  "
                  f"(with {p['margin_in']:+.1f} in {p['gp_in']}g, without {p['margin_out']:+.1f} in {p['gp_out']}g)")
        # a teammate example
        withmates = [p for p in out[team] if p["mates"]]
        if withmates:
            p = withmates[0]
            print(f"  → when {p['player']} sits, teammates:")
            for m in p["mates"][:3]:
                print(f"      {m['name']:<16} {m['pin']:.1f} -> {m['pout']:.1f} pts ({m['delta']:+.1f})")
        print()


if __name__ == "__main__":
    main()
