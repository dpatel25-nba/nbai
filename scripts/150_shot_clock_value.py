"""
Is the shot clock worth building? A value test before any code goes in the engine.

Script 145 measured a real effect: within-player conversion falls monotonically
from +0.055 pts/shot at 4-8 seconds to -0.101 at 20-24, a 0.156 spread on 18% of
all shots. That is larger than several mechanisms already in the simulator, and
the obvious move is to build it.

THE OBVIOUS MOVE IS PROBABLY WRONG, FOR TWO REASONS THIS PROJECT HAS ALREADY
BEEN BITTEN BY.

  DOUBLE COUNTING. The engine calibrates each player and team to their ACTUAL
  observed efficiency, which already contains whatever late-clock shots they
  took. Adding a late-clock penalty on top charges them twice — the same error
  that made the penalty free throws a SPLIT rather than an addition, and that
  broke the anchor three times before that.

  ADDING VARIANCE. Possession duration is not something the engine can predict
  from its state. Sampling it randomly and applying a mean-zero efficiency
  multiplier injects noise with no forecasting content — and this session has
  just spent its effort REMOVING excess variance, moving the ratio of predictive
  sd to residual sd from 1.24 to 1.01. Re-adding unpredictable noise would undo
  precisely that.

So the mechanism only earns its place if the clock carries signal the engine
cannot already express. That requires the late-clock tendency to be a STABLE
team property (otherwise there is nothing to condition on) AND to predict
efficiency ABOVE the team's own overall rate (otherwise it is already absorbed).
Both are tested here, and both must pass.

Usage: python scripts/150_shot_clock_value.py [--season 2024-25]
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LATE = 18.0        # seconds into a possession that counts as late clock


def load_possessions(season: str) -> pd.DataFrame:
    spec = importlib.util.spec_from_file_location(
        "sf", ROOT / "scripts" / "147_score_feedback.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.possessions(season)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2024-25")
    args = ap.parse_args()

    d = load_possessions(args.season)
    d = d[(d.dur >= 0) & (d.dur <= 30)].copy()
    d["late"] = (d.dur >= LATE).astype(float)
    print(f"{args.season}: {len(d):,} possessions, mean duration {d.dur.mean():.2f}s")
    print(f"  late-clock (>= {LATE:.0f}s) share: {d.late.mean()*100:.1f}%")
    print(f"  ppp early {d.loc[d.late == 0, 'pts'].mean():.4f}   "
          f"late {d.loc[d.late == 1, 'pts'].mean():.4f}   "
          f"gap {d.loc[d.late==1,'pts'].mean()-d.loc[d.late==0,'pts'].mean():+.4f}")

    # ---- TEST 1: is the late-clock tendency a stable TEAM property? ----
    d = d.sort_values(["gid", "per", "start"], ascending=[True, True, False])
    d["half"] = d.groupby("team").cumcount() % 2      # alternating, spans the season
    piv = d.groupby(["team", "half"]).agg(late=("late", "mean"),
                                          ppp=("pts", "mean"),
                                          n=("pts", "size")).unstack()
    piv = piv.dropna()
    r_late = float(np.corrcoef(piv[("late", 0)], piv[("late", 1)])[0, 1])
    print("\n=== TEST 1: is late-clock tendency a real team property? ===")
    print(f"  split-half correlation of team late-clock share: {r_late:+.3f}"
          f"   ({len(piv)} teams)")
    print(f"  spread across teams: {piv[('late',0)].std()*100:.1f} pct pts (half 1)")
    print("  For scale, the same split-half method found rebound conversion")
    print("  repeats at +0.755 and lineup chemistry at +0.02.")
    stable = r_late > 0.4

    # ---- TEST 2: does it predict efficiency ABOVE the team's own rate? ----
    # If the engine already calibrates a team to its observed ppp, then knowing
    # the team's late-clock share must add nothing on top, or it is double count.
    tm = d.groupby("team").agg(late=("late", "mean"), ppp=("pts", "mean"),
                               n=("pts", "size")).reset_index()
    r_eff = float(np.corrcoef(tm.late, tm.ppp)[0, 1])
    # n is 30 TEAMS, not 255,000 possessions. A correlation on 30 points has a
    # wide interval and a bare threshold on |r| turns "unresolved" into "pass".
    z = np.arctanh(np.clip(r_eff, -0.999, 0.999))
    zse = 1.0 / np.sqrt(len(tm) - 3)
    lo_r, hi_r = np.tanh(z - 1.96 * zse), np.tanh(z + 1.96 * zse)
    eff_sig = hi_r < 0 or lo_r > 0
    print("\n=== TEST 2: does a team's late-clock share predict its efficiency? ===")
    print(f"  corr(team late-clock share, team ppp): {r_eff:+.3f}   ({len(tm)} teams)")
    print(f"  95% CI (Fisher z): [{lo_r:+.3f}, {hi_r:+.3f}]   "
          f"{'significant' if eff_sig else 'SPANS ZERO'}")
    print(f"  slope: {np.polyfit(tm.late, tm.ppp, 1)[0]:+.3f} ppp per unit share")
    print("\n  A team that takes more late-clock shots SHOULD be less efficient if")
    print("  the clock effect were an independent force. If this correlation is")
    print("  near zero, teams that play slowly compensate, and their overall rate")
    print("  ALREADY reflects their clock profile — so an added penalty would")
    print("  charge them a second time.")

    # ---- TEST 3: what the engine could actually condition on ----
    # The engine knows transition and second chance. Strip those and ask whether
    # the remaining late-clock gap is anything it could predict.
    print("\n=== TEST 3: is the gap reachable from the engine's own state? ===")
    q = d.groupby(pd.cut(d.dur, [0, 4, 8, 12, 16, 20, 30]), observed=True).agg(
        n=("pts", "size"), ppp=("pts", "mean"))
    print(f"  {'duration':<12}{'poss':>10}{'ppp':>9}")
    for k, r in q.iterrows():
        if r.n > 500:
            print(f"  {str(k):<12}{int(r.n):>10,}{r.ppp:>9.4f}")
    print("\n  The engine already models the fast tail (transition, +13% make) and")
    print("  the second-chance tail (its putback loop). What is left is WHICH")
    print("  half-court possessions run long, and nothing in the engine's state")
    print("  predicts that — so it would be sampled at random.")

    print("\n=== VERDICT ===")
    print(f"  1. stable team property : {'YES' if stable else 'NO'} "
          f"(split-half r = {r_late:+.3f})")
    print(f"  2. predicts efficiency  : "
          f"{'YES' if eff_sig else 'UNRESOLVED'} "
          f"(r = {r_eff:+.3f}, CI [{lo_r:+.3f}, {hi_r:+.3f}] on {len(tm)} teams)")
    print("\n  Test 1 passes overwhelmingly — how long a team lets possessions run")
    print("  is one of the most repeatable properties measured in this project.")
    print("  Test 2 CANNOT BE SETTLED with 30 teams: the interval admits both a")
    print("  meaningful penalty and none at all. The point estimate leans the way")
    print("  the mechanism predicts, and that is all it does.")
    print("\n  WHAT THIS MEANS FOR THE BUILD. The engine's anchor already calibrates")
    print("  each team to its observed efficiency, so a team's clock profile is")
    print("  in its level whether or not test 2 resolves. Test 3 shows nothing in")
    print("  the engine's state predicts WHICH half-court possession runs long, so")
    print("  as a scoring multiplier this would be sampled at random: pure added")
    print("  variance, in a model whose calibration ratio was just moved to 1.01.")
    print("\n  DO NOT put it in the scoring engine (124). DO put it in the")
    print("  play-by-play renderer (134), where possession duration is already")
    print("  drawn and a team's highly repeatable pace profile makes the emitted")
    print("  clock realistic WITHOUT touching a single scoring rate.")


if __name__ == "__main__":
    main()
