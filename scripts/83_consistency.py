"""
Consistency / floor-ceiling metric — game-to-game reliability of scoring.

For each player-season, how steady is their scoring? We compute the distribution
of their points across games: floor (20th pct), ceiling (80th pct), and a
consistency score = mean / std (higher = steadier). Directly useful for props —
a high-floor player reliably clears a line; a boom/bust player is a gamble.

Output: data/parquet/consistency.parquet
Usage: python scripts/83_consistency.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "data" / "parquet" / "player_games.parquet"
OUT = ROOT / "data" / "parquet" / "consistency.parquet"


def main() -> None:
    pg = pd.read_parquet(PG, columns=["SEASON", "SEASON_TYPE", "PLAYER_ID", "firstName",
                                      "familyName", "TEAM_TRICODE", "MIN", "points"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)]

    rows = []
    for (pid, season), d in pg.groupby(["PLAYER_ID", "SEASON"]):
        if len(d) < 20:
            continue
        p = d.points.to_numpy()
        mean, std = p.mean(), p.std()
        rows.append({
            "PLAYER_ID": pid, "SEASON": season,
            "PLAYER": d.firstName.iloc[-1][0] + ". " + d.familyName.iloc[-1],
            "team": d.TEAM_TRICODE.mode().iloc[0], "GP": len(d),
            "PTS": round(mean, 1), "std": round(std, 1),
            "floor": round(np.percentile(p, 20), 1), "ceiling": round(np.percentile(p, 80), 1),
            "consistency": round(mean / std, 2) if std else np.nan,
        })
    g = pd.DataFrame(rows)
    g.to_parquet(OUT, index=False)

    print(f"Consistency metric — {len(g):,} player-seasons (>=20 games)\n")
    q = g[g.PTS >= 15]   # meaningful scorers
    print("MOST consistent scorers (>=15 ppg): reliable floor")
    print(q.nlargest(8, "consistency")[["PLAYER", "team", "SEASON", "PTS", "floor", "ceiling", "consistency"]].to_string(index=False))
    print("\nMOST boom/bust (>=15 ppg): high variance")
    print(q.nsmallest(8, "consistency")[["PLAYER", "team", "SEASON", "PTS", "floor", "ceiling", "consistency"]].to_string(index=False))
    print("\nHighest floors (>=20 ppg): safest props")
    print(g[g.PTS >= 20].nlargest(6, "floor")[["PLAYER", "team", "SEASON", "PTS", "floor", "ceiling"]].to_string(index=False))


if __name__ == "__main__":
    main()
