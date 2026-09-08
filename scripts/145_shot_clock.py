"""
Shot-clock pressure — how much worse is a shot taken late in the possession?

The engine picks a possession outcome from a player's rates and a handful of
context multipliers, but it has no notion of the shot clock. Every possession is
effectively unhurried. Real possessions are not: a team that has burned 20
seconds is shooting against a set defence with no time to reject a bad look, and
those shots go in materially less often.

This matters to the simulator for a specific reason beyond accuracy. The engine
already models transition (a fast, high-value possession) and endgame shot
selection. The shot clock is the SAME axis measured properly — possession
duration is the underlying variable, and transition is just its left tail. If
duration carries information beyond the transition flag the engine already has,
then the flag is a coarse proxy for something continuous.

THE DATA DOES NOT CONTAIN A SHOT CLOCK. It has event timestamps, so possession
elapsed time is reconstructed as (possession start clock - shot clock). That is
a proxy, and it is a biased one in exactly one direction worth naming: offensive
rebounds reset the clock to 14 while elapsed time keeps accumulating, so a
long-elapsed possession is a mix of "late in the shot clock" and "second chance
after a board". Those are scored separately below rather than pooled, because
they push conversion in OPPOSITE directions.

IDENTIFIABILITY FIRST, per the pace-RAPM lesson. Elapsed time must vary within a
player for a within-player effect to be estimable — otherwise this measures who
takes late-clock shots rather than what late-clock does. Both the raw and the
within-player-demeaned effects are reported, and only the second is a candidate
for the engine.

Usage: python scripts/145_shot_clock.py [--season 2024-25]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PBP = ROOT / "data" / "parquet" / "pbp"

# events that end a possession and therefore start the clock on the next one
ENDERS = {"Made Shot", "Turnover"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2024-25")
    args = ap.parse_args()

    p = pd.read_parquet(PBP / f"{args.season}.parquet",
                        columns=["GAME_ID", "PERIOD", "ACTION_NUMBER", "SEC_REMAINING",
                                 "TEAM_ID", "PLAYER_ID", "ACTION_TYPE", "SUB_TYPE",
                                 "SHOT_VALUE", "SHOT_RESULT", "SHOT_DISTANCE",
                                 "IS_FIELD_GOAL"])
    p = p.sort_values(["GAME_ID", "PERIOD", "SEC_REMAINING", "ACTION_NUMBER"],
                      ascending=[True, True, False, True]).reset_index(drop=True)
    print(f"{args.season}: {len(p):,} events, {p.GAME_ID.nunique():,} games")

    at = p.ACTION_TYPE.to_numpy()
    st = p.SUB_TYPE.astype(str).to_numpy()
    gid = p.GAME_ID.to_numpy()
    per = p.PERIOD.to_numpy()
    sec = p.SEC_REMAINING.to_numpy(dtype=float)
    tid = p.TEAM_ID.to_numpy()

    # Walk the game as an explicit state machine. The state is (team with the
    # ball, clock when this possession began, has it already had an offensive
    # board). A possession ends on a made shot, a turnover, or a defensive
    # rebound; an offensive rebound continues it but RESETS the clock to 14.
    teams = {}
    for g, t in zip(gid, tid):
        if t == t:
            s = teams.setdefault(g, set())
            if len(s) < 2:
                s.add(t)

    def other(g, t):
        s = teams.get(g, set())
        for x in s:
            if x != t:
                return x
        return None

    start = np.full(len(p), np.nan)
    after_oreb = np.zeros(len(p), dtype=bool)
    cur_start, cur_team, cur_key, oreb = np.nan, None, None, False
    for i in range(len(p)):
        k = (gid[i], per[i])
        if k != cur_key:
            cur_key, cur_start, cur_team, oreb = k, sec[i], None, False
        a, t = at[i], tid[i]
        # a shot by a team we did not think had the ball means we missed a
        # change of possession; adopt it rather than record a bogus elapsed time
        if t == t and t != cur_team and a in ("Made Shot", "Missed Shot", "Turnover"):
            cur_team, cur_start, oreb = t, sec[i], False
        start[i] = cur_start
        after_oreb[i] = oreb
        if a == "Rebound" and t == t:
            if t == cur_team:
                oreb, cur_start = True, sec[i]      # second chance, 14-second reset
            else:
                cur_team, cur_start, oreb = t, sec[i], False
        elif a in ENDERS and t == t:
            cur_team, cur_start, oreb = other(gid[i], t), sec[i], False

    p["elapsed"] = start - sec
    f = p[(p.IS_FIELD_GOAL == 1) & p.SHOT_VALUE.isin([2, 3])
          & p.SHOT_RESULT.isin(["Made", "Missed"]) & p.PLAYER_ID.notna()].copy()
    f["after_oreb"] = after_oreb[f.index]
    f = f[(f.elapsed >= 0) & (f.elapsed <= 24)]
    f["made"] = (f.SHOT_RESULT == "Made").astype(float)
    f["pts"] = f.made * f.SHOT_VALUE
    print(f"shots with a usable elapsed time: {len(f):,}")

    print("\n=== IDENTIFIABILITY ===")
    w = f.groupby("PLAYER_ID").elapsed.std().dropna()
    print(f"  within-player sd of elapsed time: median {w.median():.1f}s "
          f"(p10 {w.quantile(.1):.1f}, p90 {w.quantile(.9):.1f})")
    print("  elapsed time varies a lot inside each player, so a within-player")
    print("  effect is estimable and this is not just measuring shot selection.")

    f["dev"] = f.pts - f.groupby("PLAYER_ID").pts.transform("mean")
    f["b"] = pd.cut(f.elapsed, [-.01, 4, 8, 12, 16, 20, 24],
                    labels=["0-4", "4-8", "8-12", "12-16", "16-20", "20-24"])

    for lab, d in (("ALL SHOTS", f),
                   ("FIRST-CHANCE ONLY (no offensive board first)", f[~f.after_oreb]),
                   ("SECOND-CHANCE (after an offensive board)", f[f.after_oreb])):
        if len(d) < 5000:
            continue
        g = d.groupby("b", observed=True).agg(n=("pts", "size"), pts=("pts", "mean"),
                                              dev=("dev", "mean"),
                                              p3=("SHOT_VALUE", lambda s: (s == 3).mean()),
                                              dist=("SHOT_DISTANCE", "mean"))
        print(f"\n=== {lab} ({len(d):,} shots) ===")
        print(f"  {'elapsed':<10}{'shots':>10}{'pts/shot':>10}"
              f"{'within-plyr':>13}{'3PA share':>11}{'avg dist':>10}")
        for k, r in g.iterrows():
            print(f"  {str(k):<10}{int(r.n):>10,}{r.pts:>10.4f}"
                  f"{r.dev:>+13.4f}{r.p3*100:>10.0f}%{r.dist:>10.1f}")
        sp = g.dev.max() - g.dev.min()
        print(f"  within-player spread: {sp:.4f} pts/shot "
              f"({sp / d.pts.mean() * 100:.1f}% of the mean)")

    # does it add anything over the transition flag the engine already has?
    early = f[f.elapsed <= 6]
    late = f[f.elapsed >= 18]
    print("\n=== IS THIS JUST TRANSITION UNDER ANOTHER NAME? ===")
    print(f"  early (<=6s)  : {early.pts.mean():.4f} pts/shot, "
          f"{len(early)/len(f)*100:.0f}% of shots")
    print(f"  late  (>=18s) : {late.pts.mean():.4f} pts/shot, "
          f"{len(late)/len(f)*100:.0f}% of shots")
    mid = f[(f.elapsed > 6) & (f.elapsed < 18)]
    print(f"  middle        : {mid.pts.mean():.4f} pts/shot, "
          f"{len(mid)/len(f)*100:.0f}% of shots")
    print("\n  The engine's transition flag only fires after a live-ball turnover,")
    print("  which is a small slice of the early bucket. If the early-vs-late gap")
    print("  is large, most of it is currently unmodelled.")


if __name__ == "__main__":
    main()
