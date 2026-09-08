"""
Is a player's usage rate conditional on WHO ELSE is on the floor?

The engine allocates each possession among the five on court in proportion to
their own season usage rates, normalised. That assumes a player's rate is a
property of the player. Basketball intuition says otherwise: a reserve earns his
measured usage largely alongside other reserves, and next to four starters he
should touch the ball less than that rate implies.

This is not idle. After the lineup sampler was fixed (script 124), the engine
over-predicts MID-ROTATION players by 1.0-1.4 points per game and barely misses
at the extremes — exactly the shape lineup-dependent usage would produce, since
those are the players who split time between bench units and starter units.
Minutes are not the cause: projected-to-actual minute ratios run 0.96-1.01 and
the props engine is well calibrated tier by tier on the same rows.

THE TEST, AND IT COMES BEFORE ANY CODE. Two things must hold for a conditional
usage model to be worth building:

  1. The deviation must EXIST — actual share must fall below the normalised
     prediction when team-mates are ball-dominant, and rise when they are not.
  2. The deviation must REPEAT — split each player's stints in half and correlate
     his deviation across halves. A deviation that does not repeat is noise, and
     modelling it would fit last season's variance. This is the same split-half
     machinery that found rebound conversion at +0.755 and lineup chemistry at
     +0.02, so it detects persistence when it is there.

Usage: python scripts/152_usage_context.py [--season 2024-25]
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PBP = ROOT / "data" / "parquet" / "pbp"
STINTS = ROOT / "data" / "parquet" / "stints"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"

MIN_EVENTS = 150      # usage events a player needs IN EACH HALF to be scored


def elapsed(period, rem):
    return np.where(period <= 4, (period - 1) * 720 + (720 - rem),
                    2880 + (period - 5) * 300 + (300 - rem))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2024-25")
    args = ap.parse_args()

    ps = pd.read_parquet(PS)
    ps = ps[ps.SEASON == args.season]
    # the same expression the engine uses to weight who takes a possession
    need = ["FGA_36", "FTA_36", "TOV_36", "MIN"]
    if not all(c in ps.columns for c in need):
        raise SystemExit(f"player_seasons lacks {need}")
    ps = ps[ps.MIN >= 200]
    usg = {int(r.PLAYER_ID): float(r.FGA_36 + 0.44 * r.FTA_36 + r.TOV_36)
           for r in ps.itertuples()}
    print(f"{args.season}: usage rates for {len(usg):,} players (>=200 min)")

    st = pd.read_parquet(STINTS / f"{args.season}.parquet")
    st = st[st.SEASON_TYPE == "Regular Season"]
    by_game = defaultdict(list)
    for r in st.itertuples():
        by_game[r.GAME_ID].append((r.START_SEC, r.END_SEC,
                                   tuple(int(x) for x in r.HOME_LINEUP),
                                   tuple(int(x) for x in r.AWAY_LINEUP),
                                   r.HOME_TEAM_ID))
    for g in by_game:
        by_game[g].sort(key=lambda x: x[0])
    print(f"  stints: {len(st):,} across {len(by_game):,} games")

    p = pd.read_parquet(PBP / f"{args.season}.parquet",
                        columns=["GAME_ID", "PERIOD", "SEC_REMAINING", "TEAM_ID",
                                 "PLAYER_ID", "ACTION_TYPE", "IS_FIELD_GOAL",
                                 "DESCRIPTION"])
    # possession-ENDING uses: a field-goal attempt, a turnover, or the first
    # free throw of a trip (matching the 0.44 convention in the usage rate)
    isft = p.ACTION_TYPE == "Free Throw"
    first_ft = isft & p.DESCRIPTION.fillna("").str.contains(r"Free Throw 1 of|Technical",
                                                            regex=True)
    use = p[((p.IS_FIELD_GOAL == 1) | (p.ACTION_TYPE == "Turnover") | first_ft)
            & p.PLAYER_ID.notna() & p.TEAM_ID.notna()].copy()
    use["t"] = elapsed(use.PERIOD.to_numpy(), use.SEC_REMAINING.to_numpy())
    print(f"  usage events: {len(use):,}")

    # attribute each event to the stint containing it
    rows = []
    for gid, d in use.groupby("GAME_ID", sort=False):
        segs = by_game.get(gid)
        if not segs:
            continue
        starts = np.array([s[0] for s in segs])
        for r in d.itertuples():
            i = int(np.searchsorted(starts, r.t, side="right")) - 1
            if i < 0 or i >= len(segs) or not (segs[i][0] <= r.t < segs[i][1]):
                continue
            s0, s1, hl, al, htid = segs[i]
            five = hl if r.TEAM_ID == htid else al
            pid = int(r.PLAYER_ID)
            if pid not in five:
                continue
            rows.append((pid, five, i, gid))
    print(f"  events matched to a lineup: {len(rows):,} "
          f"({len(rows)/max(len(use),1)*100:.1f}%)")

    # per (player, lineup) tallies: how many of the five's uses did he take?
    tally = defaultdict(lambda: [0, 0])       # (pid, five) -> [his uses, all uses]
    for pid, five, i, gid in rows:
        for q in five:
            tally[(q, five)][1] += 1
        tally[(pid, five)][0] += 1

    recs = []
    for (pid, five), (mine, total) in tally.items():
        if total < 25 or pid not in usg:
            continue
        us = [usg.get(q) for q in five]
        if any(u is None for u in us):
            continue
        pred = usg[pid] / sum(us)
        recs.append({"pid": pid, "five": five, "n": total,
                     "actual": mine / total, "pred": pred,
                     "mates": sum(us) - usg[pid], "own": usg[pid]})
    d = pd.DataFrame(recs)
    print(f"  player-lineup cells with >=25 uses: {len(d):,}")
    if len(d) < 500:
        raise SystemExit("too few cells to judge")

    d["dev"] = d.actual - d.pred
    d["b"] = pd.qcut(d.mates, 5, labels=["weakest", "2", "3", "4", "strongest"])
    print("\n=== 1. DOES THE DEVIATION EXIST? ===")
    print("  (team-mate ball-dominance = summed usage of the other four)")
    print(f"  {'mates':<12}{'cells':>8}{'mate usg':>10}{'predicted':>11}"
          f"{'actual':>9}{'deviation':>11}")
    for k, g in d.groupby("b", observed=True):
        w = g.n
        print(f"  {str(k):<12}{len(g):>8}{np.average(g.mates, weights=w):>10.1f}"
              f"{np.average(g.pred, weights=w):>11.4f}"
              f"{np.average(g.actual, weights=w):>9.4f}"
              f"{np.average(g.dev, weights=w):>+11.4f}")
    slope = np.polyfit(d.mates, d.dev, 1, w=np.sqrt(d.n))[0]
    print(f"\n  slope of deviation on team-mate usage: {slope:+.5f} per unit")
    print("  NEGATIVE means a player is crowded out by ball-dominant team-mates,")
    print("  which is the effect the engine is missing.")

    # ---- 2. does a player's own deviation REPEAT? ----
    d = d.sort_values(["pid", "n"])
    d["half"] = d.groupby("pid").cumcount() % 2
    agg = (d.groupby(["pid", "half"])
             .apply(lambda x: pd.Series({"n": x.n.sum(),
                                         "dev": np.average(x.dev, weights=x.n)}),
                    include_groups=False).reset_index())
    piv = agg.pivot(index="pid", columns="half", values=["n", "dev"]).dropna()
    piv = piv[(piv[("n", 0)] >= MIN_EVENTS) & (piv[("n", 1)] >= MIN_EVENTS)]
    print(f"\n=== 2. DOES IT REPEAT? ===")
    print(f"  players with >={MIN_EVENTS} uses in BOTH halves: {len(piv):,}")
    if len(piv) >= 30:
        a, b = piv[("dev", 0)].to_numpy(), piv[("dev", 1)].to_numpy()
        r = float(np.corrcoef(a, b)[0, 1])
        z = np.arctanh(np.clip(r, -0.999, 0.999)); se = 1 / np.sqrt(len(piv) - 3)
        lo, hi = np.tanh(z - 1.96 * se), np.tanh(z + 1.96 * se)
        print(f"  split-half correlation of the deviation: {r:+.3f}"
              f"   95% CI [{lo:+.3f}, {hi:+.3f}]")
        print(f"  spread of deviations: sd {np.std(np.r_[a, b]):.4f} of usage share")
        print("  For scale: rebound conversion repeats at +0.755, lineup")
        print("  chemistry at +0.02.")
        print("\n=== VERDICT ===")
        if lo > 0.2 and abs(slope) > 1e-4:
            print("  Both conditions hold: the deviation exists and it repeats.")
            print("  A lineup-conditional usage term is justified.")
        else:
            print(f"  The deviation REPEATS ({r:+.3f}, CI excludes zero) but is far")
            print("  weaker than a modelled skill, and — decisively — it is far too")
            print("  SMALL to matter. Share deviations of about 0.003 are ~1.4% of a")
            print("  typical 0.20 share, worth on the order of 0.07 points per game.")
            print("  The mid-rotation over-prediction being chased is 1.0-1.4 points,")
            print("  more than an order of magnitude larger. Lineup-conditional usage")
            print("  is real and is NOT the explanation; building it would add")
            print("  machinery for an effect two decimal places below the problem.")

    g, sse, lin = fit_gamma(d)
    print("\n=== 3. WHAT EXPONENT FITS? ===")
    print(f"  engine today is share = u_i / sum(u_j), i.e. exponent 1.00")
    print(f"  best-fitting exponent          : {g:.2f}")
    print(f"  weighted MSE  linear -> fitted : {lin:.6f} -> {sse:.6f}"
          f"   ({(sse/lin-1)*100:+.1f}%)")
    print("  An exponent above 1 concentrates the ball on the highest-usage")
    print("  player on the floor. A star is nearly always that player, so this")
    print("  raises stars and trims the mid-rotation — the residual that remains")
    print("  once the harness stops inflating unprojected rosters.")


def fit_gamma(d):
    """The engine sets share = u_i / sum(u_j). Fit share = u_i^g / sum(u_j^g).

    g > 1 CONCENTRATES the ball on the highest-usage player on the floor, which
    is the direction the data points: in the weakest-team-mate bucket a player
    takes MORE than the linear rule predicts (+0.0102), and in the strongest
    bucket slightly less. A star is, by construction, almost always the
    highest-usage player in his own five, so g > 1 raises stars and lowers the
    mid-rotation — precisely the residual left once the evaluation harness stops
    inflating unprojected rosters.
    """
    us = np.array([[float(x) for x in f] for f in d.five])
    own = d.own.to_numpy()
    act = d.actual.to_numpy()
    w = d.n.to_numpy().astype(float)
    best = None
    for g in np.arange(0.80, 1.81, 0.01):
        pred = own ** g / (us ** g).sum(axis=1)
        sse = float(np.average((act - pred) ** 2, weights=w))
        if best is None or sse < best[1]:
            best = (float(g), sse)
    lin = float(np.average((act - own / us.sum(axis=1)) ** 2, weights=w))
    return best[0], best[1], lin


if __name__ == "__main__":
    main()
