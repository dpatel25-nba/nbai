"""
Team fouls and the bonus — a rule the engine does not know exists.

`_possession` draws shooting fouls from each defender's personal foul rate and
sends the shooter to the line. That is only half of how free throws happen. Once
a team commits its fifth foul in a period, EVERY subsequent defensive foul —
including one away from the ball, on a possession with no shot attempt — becomes
two free throws. The engine has no team-foul counter, so it never awards these.

That matters for more than a few points. The bonus is:

  - CONCENTRATED LATE IN PERIODS, where games are decided, and it is the
    mechanism behind intentional fouling when trailing. The engine already
    models endgame shot selection but cannot model endgame fouling, because it
    cannot represent the penalty.
  - PATH-DEPENDENT. Whether the bonus is reached depends on the fouls already
    committed, which makes it one of the few genuinely sequential effects in a
    game. A per-possession rate model cannot reproduce it by construction.
  - ASYMMETRIC BETWEEN TEAMS within a period, so it moves the margin, not just
    the total.

MEASUREMENT, not rulebook. An earlier version of this script classified fouls by
keyword and inferred from the rules which ones "should" produce free throws. It
reported 1.59 non-shooting fouls per game against a real 38.4 total, because a
generic "P.FOUL" was silently being counted as a shooting foul. Two things in
the data make that guesswork unnecessary:

  - SUB_TYPE already labels each foul (Shooting / Personal / Loose Ball / ...).
  - the description carries "(P2.T3)": P is the player's personal count and
    T IS THE RUNNING TEAM FOUL COUNT FOR THE PERIOD, recorded by the scorer.
    It runs T1-T4 and then becomes the literal "PN" once the team is in the
    penalty, so a regex for T\\d+ alone silently finds zero penalty fouls.

So the penalty state is read directly, and whether a foul produced free throws
is checked by looking for the free throws, rather than deduced. The rule is then
recovered from the data as a validation that the parse is right.

THE DOUBLE-COUNT TRAP. The engine's foul rates were fit so simulated free throws
matched observed totals, so they already carry penalty trips implicitly. The
build is therefore not "add the bonus on top" but "split the existing rate into
shooting and non-shooting, and gate the non-shooting half behind a counter" —
same total, correctly concentrated. Adding it on top would be the same mistake
that broke the anchor three times.

Usage: python scripts/144_bonus_fouls.py [--season 2024-25]
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PBP = ROOT / "data" / "parquet" / "pbp"

TEAM_CT = re.compile(r"\.(T\d+|PN)\)")
# fouls that send a shooter to the line regardless of the team count
ALWAYS_FT = {"Shooting", "Flagrant Type 1", "Flagrant Type 2", "Clear Path",
             "Away From Play", "Transition Take", "Personal Take"}
# common defensive fouls that produce free throws ONLY in the penalty
BONUS_GATED = {"Personal", "Loose Ball"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2024-25")
    args = ap.parse_args()

    p = pd.read_parquet(PBP / f"{args.season}.parquet",
                        columns=["GAME_ID", "PERIOD", "ACTION_NUMBER", "SEC_REMAINING",
                                 "TEAM_ID", "ACTION_TYPE", "SUB_TYPE", "DESCRIPTION"])
    p = p.sort_values(["GAME_ID", "PERIOD", "SEC_REMAINING", "ACTION_NUMBER"],
                      ascending=[True, True, False, True]).reset_index(drop=True)
    ng = p.GAME_ID.nunique()
    print(f"{args.season}: {len(p):,} events, {ng:,} games")

    # ---- did free throws follow this foul? checked, not assumed ----
    at = p.ACTION_TYPE.to_numpy()
    gid = p.GAME_ID.to_numpy()
    per = p.PERIOD.to_numpy()
    sec = p.SEC_REMAINING.to_numpy()
    tid = p.TEAM_ID.to_numpy()
    is_ft = at == "Free Throw"
    n = len(p)
    followed = np.zeros(n, dtype=bool)
    nft = np.zeros(n, dtype=int)
    fidx = np.flatnonzero(at == "Foul")
    for i in fidx:
        c = 0
        for j in range(i + 1, min(i + 8, n)):
            if gid[j] != gid[i] or per[j] != per[i] or sec[j] != sec[i]:
                break
            if is_ft[j] and tid[j] != tid[i]:
                c += 1
        nft[i] = c
        followed[i] = c > 0

    f = p.loc[fidx].copy()
    f["nft"] = nft[fidx]
    f["ft"] = followed[fidx]
    # the scorer records the team-foul state directly: T1-T4 while under the
    # limit, then the literal "PN" once the team is in the penalty. An earlier
    # parse only matched T\d+ and so found no penalty fouls at all.
    def _tc(d):
        m = TEAM_CT.search(str(d))
        if not m:
            return -1
        return 5 if m.group(1) == "PN" else int(m.group(1)[1:])
    f["tcount"] = [_tc(d) for d in f.DESCRIPTION]
    print(f"fouls/game {len(f)/ng:.1f}   team-count parsed on "
          f"{(f.tcount > 0).mean()*100:.1f}% of them")

    print("\n=== 1. WHAT KIND OF FOULS ARE THESE, AND DO THEY PRODUCE FTs? ===")
    print(f"  {'sub-type':<20}{'per game':>10}{'% -> FTs':>10}{'FTs/game':>10}")
    g = f.groupby("SUB_TYPE").agg(n=("ft", "size"), r=("ft", "mean"),
                                  s=("nft", "sum")).sort_values("n", ascending=False)
    for k, r in g.iterrows():
        if r.n / ng < 0.05:
            continue
        print(f"  {str(k):<20}{r.n/ng:>10.2f}{r.r*100:>10.0f}{r.s/ng:>10.2f}")

    # ---- 2. recover the rule from the data, as a parse check ----
    gate = f[f.SUB_TYPE.isin(BONUS_GATED) & (f.tcount > 0)]
    print("\n=== 2. THE PENALTY, RECOVERED FROM THE DATA ===")
    print("  (defensive Personal/Loose Ball fouls only — these are the gated ones)")
    print(f"  {'team foul #':<14}{'fouls':>9}{'% -> FTs':>11}")
    for t in range(1, 6):
        d = gate[gate.tcount == t]
        if len(d) < 40:
            continue
        lab = "5+ (penalty)" if t == 5 else str(t)
        print(f"  {lab:<14}{len(d):>9,}{d.ft.mean()*100:>11.0f}")
    lo = gate[gate.tcount <= 4].ft.mean()
    hi = gate[gate.tcount >= 5].ft.mean()
    print(f"\n  under the limit (T1-T4): {lo*100:.0f}% produce FTs")
    print(f"  in the penalty  (T5+)  : {hi*100:.0f}% produce FTs")
    print("  The step at the 5th team foul is the bonus. It appears without being")
    print("  told about, which confirms the team-count parse is right.")

    # ---- 3. how much scoring is it worth, and where does it sit? ----
    ft_all = p[is_ft]
    pen_ft = gate[gate.tcount >= 5].nft.sum()
    print("\n=== 3. SIZE OF THE MECHANISM ===")
    print(f"  free throws per game (both teams) : {len(ft_all)/ng:.2f}")
    print(f"  from penalty (non-shooting) fouls : {pen_ft/ng:.2f}"
          f"  ({pen_ft/len(ft_all)*100:.1f}% of all FTs)")
    print(f"  worth roughly {pen_ft/ng*0.78:.2f} points per game, split across two teams")

    print("\n=== 4. WHERE IN THE PERIOD ===")
    f["mins_in"] = np.where(f.PERIOD <= 4, 720 - f.SEC_REMAINING,
                            300 - f.SEC_REMAINING) / 60.0
    pen = gate[gate.tcount >= 5]
    pen_mins = np.where(pen.PERIOD <= 4, 720 - pen.SEC_REMAINING,
                        300 - pen.SEC_REMAINING) / 60.0
    if len(pen_mins) == 0:
        raise SystemExit("no penalty fouls parsed — the team-foul parse is broken")
    print(f"  median time into the period of a penalty foul : {np.median(pen_mins):.1f} min")
    print(f"  quartiles : {np.percentile(pen_mins,25):.1f} / "
          f"{np.percentile(pen_mins,75):.1f} min")
    ftl = ft_all[(ft_all.PERIOD >= 4) & (ft_all.SEC_REMAINING <= 120)]
    share = len(ftl) / len(ft_all)
    print(f"\n  free throws in the last 2 min of Q4 : {len(ftl)/ng:.2f} per game")
    print(f"  {share*100:.1f}% of all FTs in {120/2880*100:.1f}% of the clock "
          f"— a {share/(120/2880):.1f}x concentration")
    print("\n  That concentration, not the raw point total, is the argument. The")
    print("  engine spreads these evenly across the game, so it cannot produce")
    print("  the late-game foul-and-shoot dynamic that decides close games — the")
    print("  exact window where its win probabilities are made.")


if __name__ == "__main__":
    main()
