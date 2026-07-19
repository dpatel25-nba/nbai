"""
Defender quality v1 — defensive points saved per 100 possessions.

Built from the signals available back to 2013-14 (the box/tracking prior; the
D-RAPM backbone and matchup-suppression layers get added later):
  - RIM PROTECTION (tracking): points saved by holding opponents below the league
    make rate at the rim, x volume  -> real, points-valued, opponent-facing.
  - DISRUPTION: steals & blocks, valued by their possession-value swing.
  - FOUL COST (negative): fouls send opponents to the line.

Combined into DEF_VAL_100 (points saved / 100 possessions), then face-validated:
the top of the list should be the league's known elite defenders.

Honest v1 limits: box/tracking only — no on-ball matchup data (2017-18+, scraping)
and no RAPM (needs stints). Point weights are reasonable, not yet pbp-calibrated.

Usage: python scripts/80_defender_quality_v1.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "data" / "parquet" / "player_games.parquet"
OUT = ROOT / "data" / "parquet" / "defender_quality_v1.parquet"

STEAL_VAL = 1.1     # possession-value swing of a steal
BLOCK_VAL = 0.85    # a block, net of recovery
FOUL_VAL = 0.35     # points allowed per personal foul (FTs)
POSS_PER_MIN = 2.08  # ~100 poss / 48 min


def main() -> None:
    pg = pd.read_parquet(PG, columns=[
        "SEASON", "SEASON_TYPE", "PLAYER_ID", "firstName", "familyName", "TEAM_TRICODE",
        "MIN", "steals", "blocks", "foulsPersonal",
        "defendedAtRimFieldGoalsMade", "defendedAtRimFieldGoalsAttempted"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)].copy()

    g = pg.groupby(["PLAYER_ID", "SEASON"]).agg(
        name=("firstName", "first"), last=("familyName", "first"),
        team=("TEAM_TRICODE", lambda s: s.mode().iloc[0]),
        GP=("MIN", "size"), MIN=("MIN", "sum"),
        stl=("steals", "sum"), blk=("blocks", "sum"), pf=("foulsPersonal", "sum"),
        rim_made=("defendedAtRimFieldGoalsMade", "sum"),
        rim_att=("defendedAtRimFieldGoalsAttempted", "sum")).reset_index()
    g["poss"] = g.MIN * POSS_PER_MIN

    # league rim make-rate allowed per season (volume-weighted)
    lg = g.groupby("SEASON").apply(
        lambda d: d.rim_made.sum() / d.rim_att.sum(), include_groups=False).to_dict()
    g["lg_rim"] = g.SEASON.map(lg)
    g["rim_allowed"] = np.where(g.rim_att > 0, g.rim_made / g.rim_att, g.lg_rim)
    # points saved at the rim (positive = holds opponents below league make rate)
    g["rim_saved"] = (g.lg_rim - g.rim_allowed) * g.rim_att * 2

    g["DEF_VAL"] = (g.rim_saved + STEAL_VAL * g.stl + BLOCK_VAL * g.blk - FOUL_VAL * g.pf)
    g["DEF_VAL_100"] = (g.DEF_VAL / g.poss * 100).round(2)
    # component views per 100
    for c, w in [("rim_saved", 1), ("stl", STEAL_VAL), ("blk", BLOCK_VAL), ("pf", -FOUL_VAL)]:
        g[f"{c}_100"] = (g[c] * w / g.poss * 100).round(2)
    g["PLAYER"] = g["name"].str[0] + ". " + g["last"]
    g.to_parquet(OUT, index=False)

    q = g[g.MIN >= 1000]
    print(f"Defender quality v1 — {len(q):,} qualified player-seasons (>=1000 min)")
    print(f"DEF_VAL_100 range: {q.DEF_VAL_100.min():.1f} .. {q.DEF_VAL_100.max():.1f} "
          f"(mean {q.DEF_VAL_100.mean():.1f})\n")

    cols = ["PLAYER", "team", "SEASON", "DEF_VAL_100", "rim_saved_100", "blk_100", "stl_100"]
    print("=== TOP 15 defenders (all seasons) — face-validity check ===")
    print(q.nlargest(15, "DEF_VAL_100")[cols].to_string(index=False))
    print("\n=== BOTTOM 8 (should be poor defenders / negative-value bigs) ===")
    print(q.nsmallest(8, "DEF_VAL_100")[cols].to_string(index=False))


if __name__ == "__main__":
    main()
