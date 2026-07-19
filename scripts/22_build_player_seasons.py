"""
Build player_seasons.parquet: one row per player per season, the substrate for
projections, aging curves, and WAR.

Aggregates player_games (Regular Season) into season totals, per-game and per-36
rates, minutes-weighted advanced metrics, and shooting efficiency computed from
totals. Value metric (v1) = minutes-weighted PIE; swap in WAR later.

Usage: python scripts/22_build_player_seasons.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "data" / "parquet" / "player_games.parquet"
OUT = ROOT / "data" / "parquet" / "player_seasons.parquet"

# minutes-weighted advanced fields
ADV = ["offensiveRating", "defensiveRating", "netRating", "usagePercentage", "PIE", "pace"]
# counting stats we total then turn into per-game / per-36
COUNT = ["points", "reboundsTotal", "reboundsOffensive", "reboundsDefensive",
         "assists", "steals", "blocks", "turnovers", "foulsPersonal",
         "fieldGoalsMade", "fieldGoalsAttempted", "threePointersMade",
         "threePointersAttempted", "freeThrowsMade", "freeThrowsAttempted"]


def main() -> None:
    pg = pd.read_parquet(PG)
    pg = pg[pg["SEASON_TYPE"] == "Regular Season"].copy()
    played = pg[pg["MIN"] > 0].copy()

    rows = []
    for (pid, season), d in played.groupby(["PLAYER_ID", "SEASON"]):
        mins = d["MIN"].to_numpy()
        tot_min = mins.sum()
        rec = {"PLAYER_ID": pid, "SEASON": season,
               "PLAYER": f"{d['firstName'].iloc[-1]} {d['familyName'].iloc[-1]}",
               "TEAM": d["TEAM_TRICODE"].mode().iloc[0],
               "N_TEAMS": d["TEAM_TRICODE"].nunique(),
               "GP": len(d), "MIN": round(tot_min, 1),
               "MPG": round(tot_min / len(d), 1),
               "POS": d["position"].mode().iloc[0] if d["position"].notna().any() else ""}

        totals = {c: d[c].fillna(0).sum() for c in COUNT}
        for c in COUNT:
            rec[c] = totals[c]
        # shooting efficiency from totals
        fga, fta, fgm = totals["fieldGoalsAttempted"], totals["freeThrowsAttempted"], totals["fieldGoalsMade"]
        fg3m, pts = totals["threePointersMade"], totals["points"]
        rec["FG_PCT"] = round(fgm / fga, 3) if fga else np.nan
        rec["FG3_PCT"] = round(fg3m / totals["threePointersAttempted"], 3) if totals["threePointersAttempted"] else np.nan
        rec["FT_PCT"] = round(totals["freeThrowsMade"] / fta, 3) if fta else np.nan
        rec["EFG_PCT"] = round((fgm + 0.5 * fg3m) / fga, 3) if fga else np.nan
        rec["TS_PCT"] = round(pts / (2 * (fga + 0.44 * fta)), 3) if (fga + fta) else np.nan

        # minutes-weighted advanced
        for f in ADV:
            v = d[f].to_numpy(dtype=float)
            ok = ~np.isnan(v)
            rec[f.upper() if f.isupper() else f] = (
                round(float(np.sum(v[ok] * mins[ok]) / mins[ok].sum()), 2) if ok.any() and mins[ok].sum() else np.nan)

        rows.append(rec)

    df = pd.DataFrame(rows)

    # per-game and per-36 rate columns for the counting stats
    for c in COUNT:
        short = {"points": "PTS", "reboundsTotal": "REB", "reboundsOffensive": "OREB",
                 "reboundsDefensive": "DREB", "assists": "AST", "steals": "STL",
                 "blocks": "BLK", "turnovers": "TOV", "foulsPersonal": "PF",
                 "fieldGoalsMade": "FGM", "fieldGoalsAttempted": "FGA",
                 "threePointersMade": "FG3M", "threePointersAttempted": "FG3A",
                 "freeThrowsMade": "FTM", "freeThrowsAttempted": "FTA"}[c]
        df[f"{short}_PG"] = (df[c] / df["GP"]).round(1)
        df[f"{short}_36"] = (df[c] / df["MIN"] * 36).round(1)

    df = df.rename(columns={"PIE": "PIE", "pace": "PACE",
                            "offensiveRating": "ORTG", "defensiveRating": "DRTG",
                            "netRating": "NETRTG", "usagePercentage": "USG"})
    df = df.sort_values(["SEASON", "MIN"], ascending=[True, False]).reset_index(drop=True)
    df.to_parquet(OUT, index=False)

    print(f"Wrote {len(df):,} player-seasons to {OUT}")
    print(f"Seasons: {df.SEASON.nunique()} | unique players: {df.PLAYER_ID.nunique():,}")
    print("\nTop 5 by minutes-weighted PIE, 2021-22 (min 1500 MIN):")
    top = df[(df.SEASON == "2021-22") & (df.MIN >= 1500)].nlargest(5, "PIE")
    print(top[["PLAYER", "TEAM", "GP", "MPG", "PTS_PG", "REB_PG", "AST_PG", "TS_PCT", "USG", "PIE"]].to_string(index=False))


if __name__ == "__main__":
    main()
