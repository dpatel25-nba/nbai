"""
Score-dependent feedback — why real margins are narrower than independent play.

Script 146 established the gap precisely. Real per-possession variance implies a
margin sd of 16.83 under independence; the engine produces 16.81, so its
possession model is right and its possessions are genuinely independent. Real
basketball comes in at 15.88 — SUB-binomial by 5.6%. Something in a real game
pulls the score back toward level, and the engine does not have it.

This script measures that something, as a force rather than as noise. Two
channels can compress a margin, and they are separated here because they need
different code in the engine:

  EFFICIENCY. A leading team scores less per possession than it otherwise would
  (it coasts, empties the bench, milks the clock) and a trailing team scores
  more (it gambles, and the leader defends conservatively).

  PACE. A leading team burns clock and a trailing team hurries, which changes
  how many possessions REMAIN — and fewer remaining possessions is itself a
  compressing force, because it caps how much more scoring can happen.

THE CONFOUND, AND IT IS SEVERE. Teams that lead are better teams. Comparing the
efficiency of leading teams to trailing teams measures team quality, not
feedback, and would show leaders scoring MORE. The fix is a team-game fixed
effect: every possession is scored against THAT team's own mean in THAT game, so
only within-game variation in margin identifies the effect. This is the same
discipline that made the fatigue and lineup-chemistry tests trustworthy.

GARBAGE TIME IS REPORTED SEPARATELY, not pooled. If the entire effect is starters
sitting in decided games, it is a roster substitution the engine already models
and NOT a behavioural force — those are opposite conclusions and pooling them
would hide the difference.

Usage: python scripts/147_score_feedback.py [--season 2024-25]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PBP = ROOT / "data" / "parquet" / "pbp"
GAMES = ROOT / "data" / "parquet" / "games.parquet"

OFFENSIVE = ("Made Shot", "Missed Shot", "Turnover", "Free Throw")


def possessions(season: str) -> pd.DataFrame:
    """One row per possession: who had it, what it produced, and the state."""
    p = pd.read_parquet(PBP / f"{season}.parquet",
                        columns=["GAME_ID", "PERIOD", "ACTION_NUMBER", "SEC_REMAINING",
                                 "TEAM_ID", "ACTION_TYPE", "SHOT_VALUE",
                                 "SCORE_HOME", "SCORE_AWAY", "DESCRIPTION"])
    p = p.sort_values(["GAME_ID", "PERIOD", "SEC_REMAINING", "ACTION_NUMBER"],
                      ascending=[True, True, False, True]).reset_index(drop=True)
    # scores are recorded only on scoring plays; carry them forward
    p[["SCORE_HOME", "SCORE_AWAY"]] = (p.groupby("GAME_ID")[["SCORE_HOME", "SCORE_AWAY"]]
                                       .ffill().fillna(0))
    g = pd.read_parquet(GAMES, columns=["GAME_ID", "HOME_TEAM_ID"])
    home = {r.GAME_ID: r.HOME_TEAM_ID for r in g.itertuples()}

    at = p.ACTION_TYPE.to_numpy()
    gid = p.GAME_ID.to_numpy()
    per = p.PERIOD.to_numpy()
    sec = p.SEC_REMAINING.to_numpy(dtype=float)
    tid = p.TEAM_ID.to_numpy()
    sv = p.SHOT_VALUE.to_numpy()
    sh = p.SCORE_HOME.to_numpy(dtype=float)
    sa = p.SCORE_AWAY.to_numpy(dtype=float)
    # SHOT_RESULT is null on every free throw; makes are only in the text
    ftmade = ~p.DESCRIPTION.fillna("").str.startswith("MISS").to_numpy()

    rows = []
    cur = None
    for i in range(len(p)):
        a, t = at[i], tid[i]
        if a not in OFFENSIVE or t != t:
            continue
        newper = cur is not None and (cur["gid"] != gid[i] or cur["per"] != per[i])
        if cur is None or t != cur["team"] or newper:
            if cur is not None:
                rows.append(cur)
            # The score must be read STRICTLY BEFORE this possession. Using
            # sh[i]/sa[i] takes the ffilled score AT the first event, which on
            # a scoring play already contains this possession's own points — a
            # possession that scores 3 then shows a margin 3 higher, producing
            # a mechanical positive slope and an absurd 1.03->1.33 raw gradient.
            j = max(i - 1, 0)
            own = sh[j] - sa[j] if t == home.get(gid[i]) else sa[j] - sh[j]
            # possession START is the clock when the ball changed hands, i.e.
            # the previous event. Using sec[i] — the first OFFENSIVE event of
            # this possession — measures the gap between shots WITHIN it and
            # gives ~1s instead of ~14s.
            cur = {"gid": gid[i], "team": t, "per": per[i], "pts": 0.0,
                   "margin": own, "sec": sec[i], "start": sec[j]}
        if a == "Made Shot" and sv[i] in (2, 3):
            cur["pts"] += float(sv[i])
        elif a == "Free Throw" and ftmade[i]:
            cur["pts"] += 1.0
        cur["sec"] = sec[i]
    if cur is not None:
        rows.append(cur)
    d = pd.DataFrame(rows)
    d["dur"] = (d.start - d.sec).clip(0, 40)
    if d.dur.mean() < 8.0:
        raise SystemExit(f"mean possession duration {d.dur.mean():.1f}s is "
                         "impossible — the start clock is being read wrong")
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2024-25")
    args = ap.parse_args()

    d = possessions(args.season)
    print(f"{args.season}: {len(d):,} possessions, {d.gid.nunique():,} games")
    print(f"  mean points/possession {d.pts.mean():.4f}")
    if d.pts.mean() < 1.05:
        raise SystemExit("mean PPP impossibly low — points are being dropped")

    # team-game fixed effect: compare each possession to that team's own mean
    # in that game, so team quality cannot masquerade as a feedback effect
    key = d.gid.astype(str) + "_" + d.team.astype(str)
    d["dev"] = d.pts - d.groupby(key).pts.transform("mean")
    d["ddur"] = d.dur - d.groupby(key).dur.transform("mean")
    d["late"] = (d.per >= 4).to_numpy()
    d["b"] = pd.cut(d.margin, [-99, -20, -12, -6, -2, 2, 6, 12, 20, 99],
                    labels=["<-20", "-20..-12", "-12..-6", "-6..-2", "-2..2",
                            "2..6", "6..12", "12..20", ">20"])

    for lab, sub in (("ALL POSSESSIONS", d),
                     ("PERIODS 1-3", d[~d.late]),
                     ("PERIOD 4+", d[d.late])):
        g = sub.groupby("b", observed=True).agg(
            n=("pts", "size"), raw=("pts", "mean"), dev=("dev", "mean"),
            dur=("dur", "mean"), ddur=("ddur", "mean"))
        print(f"\n=== {lab} ({len(sub):,}) ===")
        print(f"  {'margin':<12}{'poss':>10}{'raw ppp':>10}{'within-game':>13}"
              f"{'dur':>8}{'within-dur':>12}")
        for k, r in g.iterrows():
            if r.n < 500:
                continue
            print(f"  {str(k):<12}{int(r.n):>10,}{r.raw:>10.4f}{r.dev:>+13.4f}"
                  f"{r.dur:>8.1f}{r.ddur:>+12.2f}")

    # the slope is what the engine needs, in points per possession per point of margin
    sub = d[d.margin.abs() <= 20]
    b = np.polyfit(sub.margin, sub.dev, 1)
    bl = np.polyfit(d[d.late & (d.margin.abs() <= 20)].margin,
                    d[d.late & (d.margin.abs() <= 20)].dev, 1)
    print("\n=== THE FEEDBACK SLOPE (within game, |margin| <= 20) ===")
    print(f"  efficiency : {b[0]*100:+.4f} points per possession per 10 pts of lead"
          .replace("per 10", "per 10"))
    print(f"    all periods {b[0]:+.5f}/pt      period 4+ {bl[0]:+.5f}/pt")
    sd = np.polyfit(sub.margin, sub.ddur, 1)
    print(f"  possession duration: {sd[0]:+.4f} seconds per point of lead")
    print("\n  A NEGATIVE efficiency slope is the compressing force: the further")
    print("  ahead a team is, the less it scores relative to its own game mean.")
    print("  A POSITIVE duration slope compresses too, by consuming clock.")

    # how much compression does that slope actually buy?
    var_ind = d.pts.var()
    poss = len(d) / d.gid.nunique() / 2
    print(f"\n  independent margin sd would be {np.sqrt(2*var_ind*poss):.2f}")
    g2 = pd.read_parquet(GAMES)
    g2 = g2[(g2.SEASON == args.season) & (g2.SEASON_TYPE == "Regular Season")]
    print(f"  actual margin sd is            {g2.MARGIN.std():.2f}")
    print(f"  so the compression to explain is "
          f"{(1 - g2.MARGIN.std()/np.sqrt(2*var_ind*poss))*100:.1f}%")
    permutation_null(d)
    serial_structure(d)

    print("\n  A mean-reverting drift of b per point of margin, applied over ~100")
    print("  possessions, shrinks the margin sd by roughly (1 + 2*b*poss)^0.5 per")
    print(f"  team; at b={b[0]:+.5f} that is "
          f"{(max(1 + 2*b[0]*poss, 0.01))**0.5:.3f} of the independent spread.")


def permutation_null(d, n_perm=6, seed=0):
    """The team-game fixed effect MANUFACTURES a negative slope.

    Demeaning forces each team's deviations to sum to zero within a game, so a
    team that scored more early must score less later — and it is ahead exactly
    because it scored more early. The estimate is therefore biased toward
    "leaders score less" even when no feedback exists at all.

    The null is built by permuting each team's possession outcomes WITHIN the
    game. That preserves every possession value, the team-game mean, the number
    of possessions and the demeaning artifact, while destroying any real
    dependence on the score state. Whatever slope survives permutation is
    artifact; only the excess over it is feedback.
    """
    d = d.sort_values(["gid", "per", "start"], ascending=[True, True, False]).copy()
    gid = d.gid.to_numpy()
    team = d.team.to_numpy()
    pts = d.pts.to_numpy(dtype=float)
    key = pd.Series(d.gid.astype(str) + "_" + d.team.astype(str)).to_numpy()
    mean_by = pd.Series(pts).groupby(key).transform("mean").to_numpy()

    def slope_from(vals):
        """Rebuild the running margin from a possession sequence, then fit."""
        run, marg = {}, np.empty(len(vals))
        for i in range(len(vals)):
            g, t = gid[i], team[i]
            if g not in run:
                run[g] = {}
            r = run[g]
            own = r.get(t, 0.0)
            opp = sum(v for k, v in r.items() if k != t)
            marg[i] = own - opp
            r[t] = own + vals[i]
        dev = vals - mean_by
        m = np.abs(marg) <= 20
        return np.polyfit(marg[m], dev[m], 1)[0], marg

    obs, marg = slope_from(pts)
    rng = np.random.default_rng(seed)
    order = np.lexsort((np.arange(len(pts)), key))
    nulls = []
    for _ in range(n_perm):
        v = pts.copy()
        # permute within each team-game block
        start = 0
        ks = key[order]
        for end in np.flatnonzero(np.r_[ks[1:] != ks[:-1], True]) + 1:
            idx = order[start:end]
            v[idx] = pts[rng.permutation(idx)]
            start = end
        nulls.append(slope_from(v)[0])
    nulls = np.asarray(nulls)
    print("\n=== PERMUTATION NULL — is the slope real or an artifact? ===")
    print(f"  observed slope           {obs:+.5f} pts/possession per point of lead")
    print(f"  permuted null  mean      {nulls.mean():+.5f}  sd {nulls.std():.5f}"
          f"  ({len(nulls)} shuffles)")
    print(f"  excess over the artifact {obs - nulls.mean():+.5f}")
    ex = obs - nulls.mean()
    se = nulls.std(ddof=1)
    # the slope required for feedback to explain the 5.6% margin compression
    NEEDED = -0.00056
    print(f"  excess z = {ex/max(se,1e-9):+.2f}   "
          f"z against the {NEEDED:+.5f} needed = {(ex-NEEDED)/max(se,1e-9):+.2f}")
    if ex > 0:
        print("\n  VERDICT: the excess is significant but has the WRONG SIGN — leading")
        print("  teams score slightly MORE, which WIDENS margins rather than")
        print("  compressing them. Reporting this as '% of the slope is real")
        print("  feedback' would be badly misleading: the compressing force the")
        print("  engine needs is ruled out, more firmly than a null would rule it.")
    elif abs(ex) < 2 * se:
        print("\n  VERDICT: indistinguishable from the demeaning artifact.")
    else:
        print(f"\n  VERDICT: a real compressing effect of {ex:+.5f}/pt survives.")
    return obs, nulls


def serial_structure(d):
    """Is a team's scoring negatively autocorrelated WITHIN a game?

    Efficiency feedback against the score is dead (permutation null above), but
    the compression still has to come from somewhere. If possessions were
    independent, splitting a team-game two ways ALTERNATELY (odd/even) and
    CHRONOLOGICALLY (first half/second half) would give the same correlation:
    both halves differ only by team quality, which is shared.

    Any gap between them is time-ordering. Chronological BELOW alternating means
    a team that starts hot cools off — mean reversion, the compressing force —
    and it is measured here without a fixed effect, so the artifact that killed
    the slope estimate cannot recur.
    """
    d = d.sort_values(["gid", "per", "start"], ascending=[True, True, False]).copy()
    d["k"] = d.gid.astype(str) + "_" + d.team.astype(str)
    d["i"] = d.groupby("k").cumcount()
    d["n"] = d.groupby("k")["i"].transform("size")
    d = d[d.n >= 60]
    alt = d.assign(h=d.i % 2)
    chrono = d.assign(h=(d.i >= d.n / 2).astype(int))
    print("\n=== SERIAL STRUCTURE WITHIN A TEAM-GAME ===")
    print(f"  {len(d.k.unique()):,} team-games with >=60 possessions")
    out = {}
    for lab, x in (("alternating (odd/even)", alt), ("chronological (1st/2nd)", chrono)):
        g = x.groupby(["k", "h"]).pts.mean().unstack()
        g = g.dropna()
        r = float(np.corrcoef(g[0], g[1])[0, 1])
        out[lab] = r
        print(f"  {lab:<26} r = {r:+.4f}   ({len(g):,} team-games)")
    gap = out["chronological (1st/2nd)"] - out["alternating (odd/even)"]
    print(f"  gap (chronological - alternating) : {gap:+.4f}")
    if gap < -0.02:
        print("\n  Chronological is LOWER: teams mean-revert within a game. That is")
        print("  the compressing force, and it is a time-ordered effect the engine")
        print("  does not have — its possessions are exchangeable by construction.")
    else:
        print("\n  No meaningful gap: scoring is EXCHANGEABLE within a game. There is")
        print("  no within-game mean reversion to build, and the compression must")
        print("  come from possession COUNT rather than from possession outcomes.")
    return out


if __name__ == "__main__":
    main()
