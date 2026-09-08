"""
Per-team possession-duration profiles for the play-by-play renderer.

Script 150 established that how long a team lets its possessions run is one of
the most repeatable properties in this project — split-half r = +0.973, against
+0.755 for rebound conversion and +0.02 for lineup chemistry. It also
established that this must NOT touch the scoring engine, because each team's
efficiency is already calibrated to include its own clock profile and the engine
cannot predict which possession runs long anyway.

The renderer (134) is a different matter. It draws a possession duration from
league-average constants scaled by a single game-level pace, so BOTH teams in a
game get identical duration distributions. A Grizzlies possession and a Magic
possession in the same game look the same, which they emphatically do not in
reality. Fixing that changes no scoring rate at all — it changes when the clock
reads what it reads, which is exactly the part of a play-by-play a reader sees.

WHAT IS PRODUCED. For each (season, team), the mean possession duration and a
multiplier relative to that season's league mean, shrunk toward 1.0 for teams
with thin samples. Also the late-clock share, for diagnostics.

WHY SHRINKAGE EVEN AT r = 0.973. Reliability that high means very little
shrinkage is warranted, and the constant here reflects that rather than a
reflexive default. It matters only for partial seasons — an in-progress season
with fifteen games should not hand the renderer a fully-trusted multiplier.

Output: data/parquet/team_clock_profile.parquet
Usage: python scripts/151_team_clock_profile.py
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PBP = ROOT / "data" / "parquet" / "pbp"
OUT = ROOT / "data" / "parquet" / "team_clock_profile.parquet"

K_SHRINK = 400.0     # possessions of league prior; small because r = 0.973
LATE = 18.0


def load_possessions(season: str) -> pd.DataFrame:
    spec = importlib.util.spec_from_file_location(
        "sf", ROOT / "scripts" / "147_score_feedback.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.possessions(season)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="")
    args = ap.parse_args()
    seasons = ([s.strip() for s in args.seasons.split(",") if s.strip()]
               or sorted(p.stem for p in PBP.glob("*.parquet")))

    rows = []
    for season in seasons:
        try:
            d = load_possessions(season)
        except (FileNotFoundError, SystemExit) as e:
            print(f"  {season}: skipped ({e})")
            continue
        d = d[(d.dur >= 0) & (d.dur <= 30)]
        if len(d) < 5000:
            print(f"  {season}: only {len(d):,} possessions, skipped")
            continue
        lg = d.dur.mean()
        g = d.groupby("team").agg(n=("dur", "size"), mean_dur=("dur", "mean"),
                                  late=("dur", lambda x: (x >= LATE).mean()),
                                  ppp=("pts", "mean")).reset_index()
        # shrink the multiplier, not the raw duration, so a thin sample lands on
        # the league pace rather than on a league duration at the wrong pace
        raw = g.mean_dur / lg
        g["mult"] = (g.n * raw + K_SHRINK * 1.0) / (g.n + K_SHRINK)
        g["SEASON"] = season
        g["LG_DUR"] = lg
        rows.append(g.rename(columns={"team": "TEAM_ID"}))
        print(f"  {season}: {len(d):,} possessions, league mean {lg:.2f}s, "
              f"team multipliers {g.mult.min():.3f}-{g.mult.max():.3f}")

    if not rows:
        raise SystemExit("no seasons produced a profile")
    out = pd.concat(rows, ignore_index=True)[
        ["SEASON", "TEAM_ID", "n", "mean_dur", "mult", "late", "ppp", "LG_DUR"]]
    out.to_parquet(OUT, index=False)
    print(f"\nSaved {len(out)} team-seasons -> {OUT}")

    last = out[out.SEASON == out.SEASON.max()].sort_values("mult")
    print(f"\n{out.SEASON.max()} — slowest and fastest possessions:")
    print(f"  {'team':>12}{'mean dur':>10}{'mult':>8}{'late %':>9}{'ppp':>8}")
    for r in pd.concat([last.head(4), last.tail(4)]).itertuples():
        print(f"  {int(r.TEAM_ID):>12}{r.mean_dur:>10.2f}{r.mult:>8.3f}"
              f"{r.late*100:>8.1f}%{r.ppp:>8.3f}")

    # year-over-year stability: does a team's profile persist into NEXT season?
    ss = sorted(out.SEASON.unique())
    if len(ss) >= 2:
        pairs = []
        for a, b in zip(ss[:-1], ss[1:]):
            m = (out[out.SEASON == a][["TEAM_ID", "mult"]]
                 .merge(out[out.SEASON == b][["TEAM_ID", "mult"]],
                        on="TEAM_ID", suffixes=("_a", "_b")))
            if len(m) > 20:
                pairs.append(np.corrcoef(m.mult_a, m.mult_b)[0, 1])
        if pairs:
            print(f"\n  year-over-year correlation of the multiplier: "
                  f"{np.mean(pairs):+.3f}  ({len(pairs)} season pairs)")
            print("  Within-season repeatability was +0.973; this is the number")
            print("  that matters for PROJECTING a team, since the renderer needs")
            print("  the profile before the season it is rendering has happened.")


if __name__ == "__main__":
    main()
