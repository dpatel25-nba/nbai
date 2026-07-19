"""
Layer-2 parser for synergyplaytypes -> player_synergy.parquet + team_synergy.parquet

One row per (player/team, season, play_type, side): frequency (POSS_PCT), efficiency
(PPP), volume (POSS/PTS), and shooting detail. This is the raw material for the
bottom-up matchup simulator — a team's offensive play-type mix vs. an opponent's
defensive PPP-allowed by play type.

Also runs a validation: do synergy-derived team ratings line up with actual team
offensive/defensive ratings? (confirms the data is meaningful before we build on it)

Usage: python scripts/99_build_synergy.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "synergy"
TG = ROOT / "data" / "parquet" / "team_games.parquet"
OUT_P = ROOT / "data" / "parquet" / "player_synergy.parquet"
OUT_T = ROOT / "data" / "parquet" / "team_synergy.parquet"

SEASONS = [f"{y}-{str(y + 1)[2:]}" for y in range(2015, 2026)]
TYPES = ["Transition", "Isolation", "PRBallHandler", "PRRollman", "Postup",
         "Spotup", "Handoff", "Cut", "OffScreen", "OffRebound", "Misc"]
KEEP = ["POSS_PCT", "PPP", "FG_PCT", "EFG_PCT", "TOV_POSS_PCT", "SCORE_POSS_PCT",
        "POSS", "PTS", "GP", "PERCENTILE"]


def parse(pot):
    rows = []
    for season in SEASONS:
        for pt in TYPES:
            for grp in ("offensive", "defensive"):
                f = RAW / f"{season}_{pt}_{grp}_{pot}.json"
                if not f.exists():
                    continue
                rs = json.loads(f.read_text())["resultSets"][0]
                df = pd.DataFrame(rs["rowSet"], columns=rs["headers"])
                if not len(df):
                    continue
                idc = "PLAYER_ID" if pot == "P" else "TEAM_ID"
                for r in df.itertuples():
                    rec = {"SEASON": season, "play_type": pt, "side": grp,
                           idc: getattr(r, idc), "TEAM_ABBREVIATION": r.TEAM_ABBREVIATION}
                    for k in KEEP:
                        rec[k.lower()] = getattr(r, k, np.nan)
                    rows.append(rec)
    return pd.DataFrame(rows)


def validate_team(team):
    """synergy team offense/defense PPP (freq-weighted) vs actual off/def rating."""
    tg = pd.read_parquet(TG)
    tg = tg[tg.SEASON_TYPE == "Regular Season"]
    actual = {}
    for (tri, s), g in tg.groupby(["TEAM_TRICODE", "SEASON"]):
        actual[(tri, s)] = (g.offensiveRating.mean(), g.defensiveRating.mean())
    # freq-weighted PPP per team-season-side
    rows = []
    for (tri, s, side), g in team.groupby(["TEAM_ABBREVIATION", "SEASON", "side"]):
        w = g.poss_pct.fillna(0)
        ppp = np.average(g.ppp.fillna(0), weights=w) if w.sum() else np.nan
        rows.append({"tri": tri, "season": s, "side": side, "syn_ppp": ppp})
    d = pd.DataFrame(rows).pivot_table(index=["tri", "season"], columns="side", values="syn_ppp").reset_index()
    d["off_actual"] = [actual.get((t, s), (np.nan, np.nan))[0] for t, s in zip(d.tri, d.season)]
    d["def_actual"] = [actual.get((t, s), (np.nan, np.nan))[1] for t, s in zip(d.tri, d.season)]
    d = d.dropna()
    ro = np.corrcoef(d.offensive, d.off_actual)[0, 1]
    rd = np.corrcoef(d.defensive, d.def_actual)[0, 1]
    return ro, rd, len(d)


def main() -> None:
    player = parse("P")
    team = parse("T")
    player.to_parquet(OUT_P, index=False)
    team.to_parquet(OUT_T, index=False)
    print(f"player_synergy: {len(player):,} rows ({player.PLAYER_ID.nunique():,} players)")
    print(f"team_synergy:   {len(team):,} rows, seasons {team.SEASON.min()}…{team.SEASON.max()}")

    ro, rd, n = validate_team(team)
    print(f"\nValidation — synergy team PPP vs actual ratings ({n} team-seasons):")
    print(f"  offense:  freq-weighted offensive PPP vs off rating  r = {ro:+.3f}")
    print(f"  defense:  freq-weighted defensive PPP vs def rating  r = {rd:+.3f}")

    # demo: the matchup grid the simulator needs — a team's offense vs a defense by play type
    latest = "2024-25"
    off = team[(team.SEASON == latest) & (team.side == "offensive") & (team.TEAM_ABBREVIATION == "OKC")]
    dfn = team[(team.SEASON == latest) & (team.side == "defensive") & (team.TEAM_ABBREVIATION == "ORL")]
    lg = team[(team.SEASON == latest) & (team.side == "defensive")].groupby("play_type").ppp.mean()
    print(f"\nMatchup-grid demo — OKC offense vs ORL defense, {latest}:")
    print(f"  {'play type':<14}{'OKC freq':>9}{'OKC PPP':>9}{'ORL D PPP':>10}{'lgD PPP':>9}")
    for pt in ["Transition", "Isolation", "PRBallHandler", "Spotup", "Postup", "Cut"]:
        o = off[off.play_type == pt]; dd = dfn[dfn.play_type == pt]
        if len(o) and len(dd):
            print(f"  {pt:<14}{o.poss_pct.iloc[0]:>9.3f}{o.ppp.iloc[0]:>9.2f}"
                  f"{dd.ppp.iloc[0]:>10.2f}{lg.get(pt, np.nan):>9.2f}")


if __name__ == "__main__":
    main()
