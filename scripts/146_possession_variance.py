"""
Per-possession points variance — the real source of the simulator's over-spread.

The variance budget (143) returned an unambiguous and inconvenient answer. With
EVERY injected noise source switched off — shared pace, shared shooting, team
shooting, usage variation, minutes jitter — the predictive margin sd falls from
16.81 to 16.70. All of the deliberately modelled game-to-game randomness is
worth 0.11 points of spread. The dispersion is essentially all possession-level
binomial noise, which means it cannot be tuned away by turning knobs down, and
an Ornstein-Uhlenbeck latent-efficiency process would push it the WRONG WAY by
adding variance the model does not need.

So the question becomes narrow and answerable: does one simulated possession
carry more points variance than one real possession? If it does, the spread is a
possession-model defect rather than a calibration choice, and it is fixable at
the source.

Both sides are measured the SAME way — points scored by the offence between
changes of possession, using the state machine from script 145 for real games
and direct instrumentation for the engine. Free throws, and-ones and putback
chains all land inside the possession that produced them, on both sides, so the
comparison is like-for-like rather than two different definitions of a
possession being compared to each other.

Usage: python scripts/146_possession_variance.py [--season 2024-25]
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PBP = ROOT / "data" / "parquet" / "pbp"

ENDERS = {"Made Shot", "Turnover"}


def real_possessions(season: str):
    """Points scored by the offence within each real possession."""
    p = pd.read_parquet(PBP / f"{season}.parquet",
                        columns=["GAME_ID", "PERIOD", "ACTION_NUMBER", "SEC_REMAINING",
                                 "TEAM_ID", "ACTION_TYPE", "SHOT_VALUE",
                                 "SHOT_RESULT", "DESCRIPTION"])
    p = p.sort_values(["GAME_ID", "PERIOD", "SEC_REMAINING", "ACTION_NUMBER"],
                      ascending=[True, True, False, True]).reset_index(drop=True)
    at = p.ACTION_TYPE.to_numpy()
    gid = p.GAME_ID.to_numpy()
    per = p.PERIOD.to_numpy()
    tid = p.TEAM_ID.to_numpy()
    sv = p.SHOT_VALUE.to_numpy()
    sr = p.SHOT_RESULT.to_numpy()
    # SHOT_RESULT is null on EVERY free throw; whether one went in is recorded
    # only as a "MISS" prefix in the description text. Reading the null column
    # dropped all 57,309 free throws and put the mean at 0.983 instead of 1.135.
    ftmade = ~p.DESCRIPTION.fillna("").str.startswith("MISS").to_numpy()

    teams = {}
    for g, t in zip(gid, tid):
        if t == t:
            s = teams.setdefault(g, set())
            if len(s) < 2:
                s.add(t)

    def other(g, t):
        for x in teams.get(g, ()):
            if x != t:
                return x
        return None

    # A possession runs until the OFFENSIVE TEAM CHANGES. Closing it eagerly on
    # a made shot instead splits and-one and shooting-foul free throws into the
    # opponent's next possession, which silently deleted every free throw: the
    # mean came out 0.984 instead of 1.135, exactly the ~17 points per team that
    # free throws are worth. Offensive rebounds need no special case either —
    # the team has not changed, so the possession simply continues.
    OFFENSIVE = ("Made Shot", "Missed Shot", "Turnover", "Free Throw")
    out = []
    cur_team, cur_key, acc, live = None, None, 0.0, False
    for i in range(len(p)):
        a, t = at[i], tid[i]
        k = (gid[i], per[i])
        if k != cur_key:
            if live:
                out.append(acc)
            cur_key, cur_team, acc, live = k, None, 0.0, False
        if a not in OFFENSIVE or t != t:
            continue
        if t != cur_team:
            if live:
                out.append(acc)
            cur_team, acc, live = t, 0.0, True
        if a == "Made Shot" and sv[i] in (2, 3):
            acc += float(sv[i])
        elif a == "Free Throw" and ftmade[i]:
            acc += 1.0
    if live:
        out.append(acc)
    return np.asarray(out, dtype=float), p.GAME_ID.nunique()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2024-25")
    args = ap.parse_args()

    v, ng = real_possessions(args.season)
    v = v[(v >= 0) & (v <= 10)]
    print(f"{args.season}: {len(v):,} real possessions across {ng:,} games "
          f"({len(v)/ng/2:.1f} per team-game)")
    print(f"\n  mean points per possession : {v.mean():.4f}")
    print(f"  variance                   : {v.var():.4f}")
    print(f"  sd                         : {v.std():.4f}")
    print("\n  outcome distribution:")
    if v.mean() < 1.05:
        raise SystemExit(f"mean PPP {v.mean():.3f} is impossibly low — points are "
                         "being dropped; refusing to report a variance from it")
    u, c = np.unique(v, return_counts=True)
    for a, b in zip(u, c):
        if b / len(v) >= 0.004:
            print(f"    {a:>4.0f} pts  {b/len(v)*100:>6.2f}%")

    poss = len(v) / ng / 2
    print(f"\n  If possessions were INDEPENDENT, a team's score sd would be")
    print(f"  sqrt(var * poss) = {np.sqrt(v.var()*poss):.2f} and the margin sd")
    print(f"  sqrt(2 * var * poss) = {np.sqrt(2*v.var()*poss):.2f}.")

    g = pd.read_parquet(ROOT / "data" / "parquet" / "games.parquet")
    g = g[(g.SEASON == args.season) & (g.SEASON_TYPE == "Regular Season")]
    print(f"  Actual margin sd is {g.MARGIN.std():.2f}, so real basketball is")
    print(f"  {'SUB' if g.MARGIN.std() < np.sqrt(2*v.var()*poss) else 'SUPER'}"
          f"-binomial by a factor of "
          f"{g.MARGIN.std()/np.sqrt(2*v.var()*poss):.3f}.")
    print("\n  THE TARGET FOR THE ENGINE is not this unconditional margin sd. A")
    print("  forecast that knows the rosters should be NARROWER than the")
    print("  league-wide spread: var(margin) = var(predictable) + E[var|matchup].")
    sd_pred = 6.0
    print(f"  With a point-spread sd of about {sd_pred:.0f}, the conditional")
    print(f"  target is sqrt({g.MARGIN.std():.2f}^2 - {sd_pred:.0f}^2) = "
          f"{np.sqrt(g.MARGIN.std()**2 - sd_pred**2):.2f}.")
    print(f"  The engine currently produces 16.81, and its floor with all")
    print(f"  injected noise removed is 16.70 (script 143).")


if __name__ == "__main__":
    main()
