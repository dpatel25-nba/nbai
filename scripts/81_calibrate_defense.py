"""
Calibrate defender-quality event weights from OUR play-by-play — the "our own way"
refinement of v1's borrowed constants.

Measures from our data:
  - PPP (points per possession) = the value of a defensive stop
  - block recovery rate (block -> next rebound goes to the defense?) from pbp
  - points allowed per personal foul (FT points / fouls) from box
Then sets data-grounded weights and rebuilds DEF_VAL_100, showing how it shifts.

Usage: python scripts/81_calibrate_defense.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "data" / "parquet" / "game_logs.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
PBP = ROOT / "data" / "parquet" / "pbp"
OUT = ROOT / "data" / "parquet" / "defender_quality.parquet"
POSS_PER_MIN = 2.08


def measure_ppp_and_fouls():
    lg = pd.read_parquet(LOGS, columns=["PTS", "FGA", "FTA", "FTM", "OREB", "TOV", "PF"])
    poss = (lg.FGA + 0.44 * lg.FTA - lg.OREB + lg.TOV).sum()
    ppp = lg.PTS.sum() / poss
    pts_per_foul = lg.FTM.sum() / lg.PF.sum()
    return ppp, pts_per_foul


def measure_block_recovery():
    defrec = total = 0
    for season in ["2017-18", "2018-19", "2019-20"]:
        p = pd.read_parquet(PBP / f"{season}.parquet",
                            columns=["GAME_ID", "ACTION_NUMBER", "ACTION_TYPE",
                                     "TEAM_TRICODE", "DESCRIPTION"]).sort_values(
            ["GAME_ID", "ACTION_NUMBER"]).reset_index(drop=True)
        is_block = p.DESCRIPTION.str.contains("BLOCK", case=False, na=False).to_numpy()
        at = p.ACTION_TYPE.to_numpy(); tri = p.TEAM_TRICODE.to_numpy(); gid = p.GAME_ID.to_numpy()
        for i in np.where(is_block)[0]:
            blocker_team = tri[i]
            # next rebound in same game
            for j in range(i + 1, min(i + 6, len(p))):
                if gid[j] != gid[i]:
                    break
                if at[j] == "Rebound":
                    total += 1
                    if tri[j] == blocker_team:   # defense (blocker's team) recovered -> stop
                        defrec += 1
                    break
    return defrec / total if total else 0.55


def main() -> None:
    ppp, pts_per_foul = measure_ppp_and_fouls()
    recov = measure_block_recovery()
    # data-grounded weights
    STEAL = ppp + 0.15                    # prevents a possession (~PPP) + transition premium
    BLOCK = recov * 1.15                  # recovered blocks prevent a ~rim-value shot
    FOUL = pts_per_foul                   # points conceded at the line per foul

    print("CALIBRATED FROM OUR DATA:")
    print(f"  points per possession (PPP)  {ppp:.3f}")
    print(f"  block recovery rate          {recov:.3f}  (defense keeps the ball)")
    print(f"  points per personal foul     {pts_per_foul:.3f}")
    print(f"  -> weights: steal {STEAL:.2f} | block {BLOCK:.2f} | foul -{FOUL:.2f}")
    print(f"     (v1 borrowed: steal 1.10 | block 0.85 | foul -0.35)\n")

    pg = pd.read_parquet(PG, columns=["SEASON", "SEASON_TYPE", "PLAYER_ID", "firstName",
                                      "familyName", "TEAM_TRICODE", "MIN", "steals", "blocks",
                                      "foulsPersonal", "defendedAtRimFieldGoalsMade",
                                      "defendedAtRimFieldGoalsAttempted"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)]
    g = pg.groupby(["PLAYER_ID", "SEASON"]).agg(
        nm=("firstName", "first"), ln=("familyName", "first"),
        team=("TEAM_TRICODE", lambda s: s.mode().iloc[0]),
        MIN=("MIN", "sum"), stl=("steals", "sum"), blk=("blocks", "sum"),
        pf=("foulsPersonal", "sum"), rim_made=("defendedAtRimFieldGoalsMade", "sum"),
        rim_att=("defendedAtRimFieldGoalsAttempted", "sum")).reset_index()
    g["poss"] = g.MIN * POSS_PER_MIN
    lg_rim = g.groupby("SEASON").apply(lambda d: d.rim_made.sum() / d.rim_att.sum(),
                                       include_groups=False).to_dict()
    g["lg_rim"] = g.SEASON.map(lg_rim)
    g["rim_allowed"] = np.where(g.rim_att > 0, g.rim_made / g.rim_att, g.lg_rim)
    g["rim_saved"] = (g.lg_rim - g.rim_allowed) * g.rim_att * 2
    g["DEF_VAL_100"] = ((g.rim_saved + STEAL * g.stl + BLOCK * g.blk - FOUL * g.pf)
                        / g.poss * 100).round(2)
    g["PLAYER"] = g["nm"].str[0] + ". " + g["ln"]
    g.to_parquet(OUT, index=False)

    q = g[g.MIN >= 1000]
    print(f"Calibrated defender quality — {len(q):,} qualified seasons "
          f"(range {q.DEF_VAL_100.min():.1f}..{q.DEF_VAL_100.max():.1f})\n")
    print("TOP 12 (calibrated):")
    print(q.nlargest(12, "DEF_VAL_100")[["PLAYER", "team", "SEASON", "DEF_VAL_100"]].to_string(index=False))


if __name__ == "__main__":
    main()
