"""
Lineup interaction — are five players additive, or is there chemistry?

The possession engine treats a lineup as the sum of its members: usage, rates and
impact all add up, and no unit is worth more or less than its parts. That is an
assumption it has never checked, and "lineup chemistry" is one of the most
commonly asserted effects in basketball.

THE IDENTIFIABILITY PROBLEM COMES FIRST, per the lesson from the pace RAPM.
Lineup-specific effects are trivially easy to "find" and almost impossible to
measure: most five-man units play a few dozen possessions, so the gap between a
lineup's actual net rating and the sum of its parts is dominated by noise. Any
regression will happily fit that noise and report large chemistry effects.

So the test is not "do lineups deviate from the sum of their parts" — they always
will. The test is WHETHER THE DEVIATION REPEATS:

    split each lineup's stints into two halves (alternating, so both halves span
    the season), compute the residual against the additive prediction in each,
    and correlate them

A real chemistry effect shows up in both halves. Noise does not. This is the same
split-half logic that showed rebound conversion is a genuine skill (+0.755), so
the method is capable of detecting persistence when it exists — which matters,
because a null is only informative if the test could have found something.

The additive baseline uses the RAPM values from script 135, which are themselves
estimated from these stints and sum to team net rating by construction.

Usage: python scripts/140_lineup_interaction.py
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STINTS = ROOT / "data" / "parquet" / "stints"
RAPM = ROOT / "data" / "parquet" / "rapm.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
SEASONS = ["2023-24", "2024-25"]
MIN_POSS = 100        # possessions a lineup needs in EACH half to be scored


def main() -> None:
    if not RAPM.exists():
        raise SystemExit("run scripts/135_rapm.py first")
    rap = pd.read_parquet(RAPM)
    R = {int(r.PLAYER_ID): float(r.RAPM) for r in rap.itertuples()}

    fr = []
    for s in SEASONS:
        f = STINTS / f"{s}.parquet"
        if f.exists():
            d = pd.read_parquet(f)
            fr.append(d[d.SEASON_TYPE == "Regular Season"])
    st = pd.concat(fr, ignore_index=True)
    print(f"stints: {len(st):,} across {st.GAME_ID.nunique():,} games")

    # one row per (lineup, opponent-lineup) side of every stint
    rows = []
    for r in st.itertuples():
        for own, opp, pts, apts, poss in (
                (r.HOME_LINEUP, r.AWAY_LINEUP, r.HOME_PTS, r.AWAY_PTS, r.HOME_POSS),
                (r.AWAY_LINEUP, r.HOME_LINEUP, r.AWAY_PTS, r.HOME_PTS, r.AWAY_POSS)):
            if poss < 1:
                continue
            key = tuple(sorted(int(p) for p in own))
            if any(p not in R for p in key):
                continue
            opp_r = [R[int(p)] for p in opp if int(p) in R]
            if len(opp_r) < 5:
                continue
            rows.append({"lineup": key, "poss": poss,
                         "margin": (pts - apts) / poss * 100.0,
                         "pred": sum(R[p] for p in key) - sum(opp_r)})
    d = pd.DataFrame(rows)
    d["resid"] = d.margin - d.pred
    print(f"scored lineup-stints: {len(d):,}   distinct lineups: {d.lineup.nunique():,}")

    # alternate stints into two halves so both span the season
    d["half"] = d.groupby("lineup").cumcount() % 2
    agg = (d.groupby(["lineup", "half"])
             .apply(lambda x: pd.Series({"poss": x.poss.sum(),
                                         "resid": np.average(x.resid, weights=x.poss)}),
                    include_groups=False)
             .reset_index())
    piv = agg.pivot(index="lineup", columns="half", values=["poss", "resid"]).dropna()
    piv = piv[(piv[("poss", 0)] >= MIN_POSS) & (piv[("poss", 1)] >= MIN_POSS)]
    print(f"lineups with >={MIN_POSS} possessions in BOTH halves: {len(piv):,}")
    if len(piv) < 30:
        print("too few to judge — chemistry is not measurable at this sample size,")
        print("which is itself the answer for a simulator that must run any lineup")
        return

    a = piv[("resid", 0)].to_numpy()
    b = piv[("resid", 1)].to_numpy()
    w = np.minimum(piv[("poss", 0)], piv[("poss", 1)]).to_numpy()
    r = float(np.corrcoef(a, b)[0, 1])
    print(f"\n=== SPLIT-HALF PERSISTENCE OF LINEUP RESIDUALS ===")
    print(f"  correlation between halves : {r:+.3f}")
    print(f"  spread of residuals        : sd {np.std(np.r_[a, b]):.2f} pts/100")
    print(f"  weighted correlation       : "
          f"{np.corrcoef(a * np.sqrt(w), b * np.sqrt(w))[0,1]:+.3f}")
    print(f"\n  For scale, the same split-half method found rebound conversion")
    print(f"  repeats at +0.755, so it detects persistence when it is there.")
    if r < 0.15:
        print("\n  VERDICT: lineup chemistry does not repeat. The deviation from the")
        print("  sum of the parts is sampling noise, and an engine that modelled it")
        print("  would be fitting last season's variance. Additive stays.")
    else:
        print("\n  VERDICT: some persistence — worth modelling, with heavy shrinkage.")

    # is any of it explained by something structural rather than chemistry?
    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "POS"])
    pos = {}
    for r2 in ps.itertuples():
        pos.setdefault(int(r2.PLAYER_ID), r2.POS)

    def bigs(lu):
        return sum(1 for p in lu if isinstance(pos.get(p), str) and "C" in pos.get(p, ""))
    # the pivot leaves MultiIndex columns; flatten before grouping
    flat = pd.DataFrame({
        "bigs": [bigs(l) for l in piv.index],
        "mres": ((piv[("resid", 0)] + piv[("resid", 1)]) / 2).to_numpy()})
    g = flat.groupby("bigs").agg(n=("mres", "size"), resid=("mres", "mean"))
    print("\n  Residual by number of centres on the floor (a structural check):")
    for i, row in g.iterrows():
        if row.n >= 15:
            print(f"    {int(i)} centre(s): {int(row.n):>4} lineups   "
                  f"mean residual {row.resid:+.2f}")


if __name__ == "__main__":
    main()
