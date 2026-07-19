"""
Defender quality v2 — opponent-adjusted matchup suppression (perimeter defense).

v1 was rim-centric (blocks/steals/fouls) and missed pure perimeter stoppers.
The matchup data lets us measure the thing that actually matters: does an
offensive player score LESS than usual when THIS defender guards him?

Method (difficulty-adjusted, like defensive FG% but for points):
  1. Each offender's baseline scoring rate = points they scored per partial
     possession across ALL defenders that season (how hard they are to guard).
  2. For a defender, EXPECTED points = sum over his matchups of
     (partial_poss * that offender's baseline rate).  Guarding stars costs more.
  3. ALLOWED = points those offenders actually scored on him.
  4. SUPPRESSION = (expected - allowed) / partial_poss * 100
     = points saved per 100 matchup possessions. Positive = good defender.
  Same idea for FG% allowed vs. each offender's baseline FG%.

Requires enough guarded possessions to report. Output: defender_quality_v2.parquet
Usage: python scripts/87_defender_quality_v2.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MU = ROOT / "data" / "parquet" / "matchups.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
OUT = ROOT / "data" / "parquet" / "defender_quality_v2.parquet"
MIN_POSS = 800     # min guarded partial possessions in a season to report
LG_PPP = None      # league avg points per partial possession (filled at runtime)


def main() -> None:
    df = pd.read_parquet(MU)
    df = df[df.SEASON_TYPE == "Regular Season"].copy()

    # 1. offender baseline rates per season (their scoring/shooting when guarded)
    off = df.groupby(["OFF_ID", "SEASON"]).agg(
        pp=("partial_poss", "sum"), pts=("pts_allowed", "sum"),
        fgm=("fgm", "sum"), fga=("fga", "sum")).reset_index()
    off["off_ppp"] = off.pts / off.pp
    off["off_fg"] = np.where(off.fga >= 20, off.fgm / off.fga, np.nan)
    # shrink FG baseline toward league mean for thin samples
    lg_fg = off.fgm.sum() / off.fga.sum()
    off["off_fg"] = off.off_fg.fillna(lg_fg)
    ppp = {(r.OFF_ID, r.SEASON): r.off_ppp for r in off.itertuples()}
    ofg = {(r.OFF_ID, r.SEASON): r.off_fg for r in off.itertuples()}

    df["exp_pts"] = [ppp[(o, s)] * p for o, s, p in
                     zip(df.OFF_ID, df.SEASON, df.partial_poss)]
    df["exp_fgm"] = [ofg[(o, s)] * a for o, s, a in zip(df.OFF_ID, df.SEASON, df.fga)]

    # 2-4. aggregate per defender-season
    d = df.groupby(["DEF_ID", "SEASON"]).agg(
        pp=("partial_poss", "sum"), allowed=("pts_allowed", "sum"),
        exp=("exp_pts", "sum"), fga=("fga", "sum"), fgm=("fgm", "sum"),
        exp_fgm=("exp_fgm", "sum"), fg3a=("fg3a", "sum"), fg3m=("fg3m", "sum"),
        blk=("blk", "sum"), tov=("tov", "sum"), games=("GAME_ID", "nunique")).reset_index()
    d = d[d.pp >= MIN_POSS].copy()

    # points saved per 100 matchup possessions (opponent-adjusted)
    d["suppression"] = (d.exp - d.allowed) / d.pp * 100
    # FG% allowed vs expected (negative = held below expectation = good)
    d["dfg_delta"] = np.where(d.fga >= 100, (d.fgm - d.exp_fgm) / d.fga, np.nan)
    d["allowed_p100"] = d.allowed / d.pp * 100

    # attach minutes/names (needed for the assignment control)
    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "PLAYER", "SEASON", "TEAM", "MPG"])
    name = {(r.PLAYER_ID, r.SEASON): (r.PLAYER, r.TEAM, r.MPG) for r in ps.itertuples()}
    d["PLAYER"] = [name.get((i, s), ("?", "?", np.nan))[0] for i, s in zip(d.DEF_ID, d.SEASON)]
    d["TEAM"] = [name.get((i, s), ("?", "?", np.nan))[1] for i, s in zip(d.DEF_ID, d.SEASON)]
    d["MPG"] = [name.get((i, s), ("?", "?", np.nan))[2] for i, s in zip(d.DEF_ID, d.SEASON)]

    # --- KEY STEP: control for assignment. Raw suppression correlates -0.59 with
    # minutes — bench players hidden on weak scorers "suppress" a lot. Residualize
    # suppression on (minutes, assignment difficulty) within each season to isolate
    # the actual defensive SKILL. This turns the leaderboard from role-players +
    # hidden defenders into a credible list (Draymond, Caruso, Horford, DFS...).
    d["assign_diff"] = d.exp / d.pp * 100          # avg baseline pts/100 of who they guard
    d = d.dropna(subset=["MPG"]).copy()

    def residual_skill(g):
        X = np.column_stack([np.ones(len(g)), g["MPG"].to_numpy(), g["assign_diff"].to_numpy()])
        b, *_ = np.linalg.lstsq(X, g["suppression"].to_numpy(), rcond=None)
        return pd.Series(g["suppression"].to_numpy() - X @ b, index=g.index)

    d["skill"] = d.groupby("SEASON", group_keys=False).apply(residual_skill)
    # DEF_RATING = per-season z of skill, scaled to a readable points-saved flavour
    def zscore(g):
        v = g["skill"]; sd = v.std(ddof=0)
        return (v - v.mean()) / sd * 2.0 if sd else v * 0
    d["DEF_RATING"] = d.groupby("SEASON", group_keys=False).apply(zscore).round(2)

    d.rename(columns={"DEF_ID": "PLAYER_ID"}).to_parquet(OUT, index=False)
    print(f"defender_quality_v2: {len(d):,} defender-seasons "
          f"(≥{MIN_POSS} poss), seasons {d.SEASON.min()}…{d.SEASON.max()}")
    print(f"  assignment-adjusted skill vs MPG corr: {np.corrcoef(d.skill, d.MPG)[0,1]:+.3f} "
          f"(confound removed)\n")

    latest = d.SEASON.max()
    print(f"=== Top 15 perimeter defenders, {latest} (assignment-adjusted skill) ===")
    for r in d[(d.SEASON == latest) & (d.pp >= 1200)].nlargest(15, "DEF_RATING").itertuples():
        print(f"  {r.PLAYER:<22} {r.TEAM:<4} DEF {r.DEF_RATING:+.2f}  "
              f"({r.MPG:.0f}mpg, faces {r.assign_diff:.0f}/100, {r.pp:,.0f} poss)")

    print(f"\n=== Worst 8 (targeted / hunted on D), {latest} — note the star-scorer/big skew ===")
    for r in d[(d.SEASON == latest) & (d.pp >= 1200)].nsmallest(8, "DEF_RATING").itertuples():
        print(f"  {r.PLAYER:<22} {r.TEAM:<4} DEF {r.DEF_RATING:+.2f}  ({r.MPG:.0f}mpg)")


if __name__ == "__main__":
    main()
