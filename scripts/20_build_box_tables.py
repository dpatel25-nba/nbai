"""
Build Layer 2 box-score tables from raw per-game JSON:
  - player_games.parquet : one row per player per game (traditional + advanced
                           + tracking merged on GAME_ID + PLAYER_ID)
  - team_games.parquet   : one row per team per game (team traditional + advanced)

Idempotent & resumable-friendly: parses whatever games have a traditional box
file on disk right now. Games missing the advanced/tracking file still produce
rows (those columns are null) so nothing is dropped; re-run after more scraping
to rebuild with the full set. Join keys and datetimes follow project convention.

Usage: python scripts/20_build_box_tables.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT_PLAYERS = ROOT / "data" / "parquet" / "player_games.parquet"
OUT_TEAMS = ROOT / "data" / "parquet" / "team_games.parquet"

IDENTITY = ["firstName", "familyName", "nameI", "position", "jerseyNum", "comment"]
# stats to drop from the secondary sources because they duplicate traditional
ADV_DROP = {"minutes"}
TRK_DROP = {"minutes", "assists", "fieldGoalPercentage"}


def parse_min(s) -> float:
    """'36:05' or ISO 'PT36M05.00S' -> minutes as float. None/'' -> 0.0."""
    if not s:
        return 0.0
    s = str(s)
    if ":" in s:
        m, sec = s.split(":")
        return int(m) + float(sec) / 60.0
    if "PT" in s:
        mm = re.search(r"(\d+)M", s)
        ss = re.search(r"([\d.]+)S", s)
        return (int(mm.group(1)) if mm else 0) + (float(ss.group(1)) if ss else 0) / 60.0
    return 0.0


def load(ep: str, key: str, gid: str):
    p = RAW / ep / f"{gid}.json"
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)[key]


def player_stats_by_id(payload, drop: set) -> dict:
    """personId -> {stat: val} for both teams, dropping overlap columns."""
    out = {}
    if payload is None:
        return out
    for side in ("homeTeam", "awayTeam"):
        for pl in payload[side]["players"]:
            out[pl["personId"]] = {k: v for k, v in pl["statistics"].items()
                                   if k not in drop}
    return out


def main() -> None:
    games = pd.read_parquet(GAMES)
    meta = {r.GAME_ID: r for r in games.itertuples()}

    gids = sorted(f.stem for f in (RAW / "boxscoretraditionalv3").glob("*.json"))
    print(f"traditional box files on disk: {len(gids):,}")

    player_rows, team_rows = [], []
    miss_adv = miss_trk = 0

    for gid in gids:
        trad = load("boxscoretraditionalv3", "boxScoreTraditional", gid)
        if trad is None or gid not in meta:
            continue
        adv = load("boxscoreadvancedv3", "boxScoreAdvanced", gid)
        trk = load("boxscoreplayertrackv3", "boxScorePlayerTrack", gid)
        miss_adv += adv is None
        miss_trk += trk is None

        adv_by_id = player_stats_by_id(adv, ADV_DROP)
        trk_by_id = player_stats_by_id(trk, TRK_DROP)

        m = meta[gid]
        for side in ("homeTeam", "awayTeam"):
            team = trad[side]
            tid = team["teamId"]
            home_away = "HOME" if tid == m.HOME_TEAM_ID else "AWAY"
            opp = m.AWAY_TEAM_ID if home_away == "HOME" else m.HOME_TEAM_ID
            base = dict(GAME_ID=gid, GAME_DATE=m.GAME_DATE, SEASON=m.SEASON,
                        SEASON_TYPE=m.SEASON_TYPE, TEAM_ID=tid, OPP_TEAM_ID=opp,
                        HOME_AWAY=home_away, TEAM_TRICODE=team["teamTricode"])

            # --- players ---
            for pl in team["players"]:
                pid = pl["personId"]
                row = {**base, "PLAYER_ID": pid}
                for k in IDENTITY:
                    row[k] = pl.get(k)
                st = pl["statistics"]
                row["MIN"] = parse_min(st.get("minutes"))
                row.update(st)                       # traditional stats
                row.update(adv_by_id.get(pid, {}))   # advanced
                row.update(trk_by_id.get(pid, {}))   # tracking
                player_rows.append(row)

            # --- team totals ---
            trow = {**base}
            trow["TEAM_NAME"] = team.get("teamName")
            trow["MIN"] = parse_min(team["statistics"].get("minutes"))
            trow.update(team["statistics"])
            if adv is not None:
                trow.update({k: v for k, v in adv[side]["statistics"].items()
                             if k not in ADV_DROP})
            team_rows.append(trow)

    players = pd.DataFrame(player_rows)
    teams = pd.DataFrame(team_rows)
    for df in (players, teams):
        df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"])

    players.to_parquet(OUT_PLAYERS, index=False)
    teams.to_parquet(OUT_TEAMS, index=False)

    print(f"\nplayer_games: {len(players):,} rows, {players.shape[1]} cols "
          f"({players.GAME_ID.nunique():,} games)")
    print(f"team_games:   {len(teams):,} rows, {teams.shape[1]} cols")
    print(f"games missing advanced: {miss_adv} | missing tracking: {miss_trk}")
    print(f"\nwrote {OUT_PLAYERS.name} and {OUT_TEAMS.name}")


if __name__ == "__main__":
    main()
