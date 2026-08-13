"""
Pace RAPM, done correctly — possession DURATION, not possession count.

Script 135's pace regression was mis-specified and its output was nonsense (Ivica
Zubac came out as the league's fastest player). The diagnosis is structural, not
statistical:

    POSSESSION COUNT IS SHARED BETWEEN THE TWO TEAMS BY CONSTRUCTION.

Teams alternate, so within any stint HOME_POSS - AWAY_POSS has mean -0.018 and
the two correlate at 0.81. Regressing "stint pace" on player indicators asks how
each player changes a number that is, by the rules of basketball, the same for
both teams. No penalty or specification fixes that.

What genuinely varies per team is DURATION: team A can average 16 seconds a
possession while team B averages 8, with both taking the same NUMBER. That signal
exists only at possession level in the play-by-play, never in stint aggregates.

So this rebuilds it properly:

    one row per possession, y = its duration in seconds
    X = the five on OFFENCE (who choose when to shoot)
        + the five on DEFENCE (pressure can force early or late shots)
        + fixed effects for how the possession started, since that dominates
          everything else (8.9s after a live turnover vs 16.3s after a make)

Offensive and defensive coefficients are estimated separately, because deciding
to push and forcing someone else to rush are different skills and there is no
reason they should share a parameter.

Validated on a season the model never saw: does a lineup's predicted duration
beat simply using the two teams' averages?

Usage: python scripts/136_pace_rapm.py
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix, hstack
from scipy.sparse.linalg import lsqr

ROOT = Path(__file__).resolve().parents[1]
PBP = ROOT / "data" / "parquet" / "pbp"
STINTS = ROOT / "data" / "parquet" / "stints"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
OUT = ROOT / "data" / "parquet" / "pace_rapm.parquet"

TRAIN = ["2023-24", "2024-25"]
TEST = "2025-26"
LIVE = ("lost ball", "bad pass")
STARTS = ["make", "rebound", "live_to", "dead_to"]
LAMBDAS = [200, 500, 1000, 2500, 6000, 15000]


def elapsed(period, rem):
    return ((period - 1) * 720.0 + (720.0 - rem) if period <= 4
            else 2880.0 + (period - 5) * 300.0 + (300.0 - rem))


def possessions(season):
    """-> DataFrame of possessions with duration, offensive team, start type."""
    p = pd.read_parquet(PBP / f"{season}.parquet",
                        columns=["GAME_ID", "PERIOD", "ACTION_NUMBER", "SEC_REMAINING",
                                 "TEAM_ID", "ACTION_TYPE", "DESCRIPTION"])
    p = p.sort_values(["GAME_ID", "PERIOD", "SEC_REMAINING", "ACTION_NUMBER"],
                      ascending=[True, True, False, True]).reset_index(drop=True)
    rows, prev, gid_prev = [], None, None
    for r in p.itertuples():
        if r.GAME_ID != gid_prev or r.ACTION_TYPE == "period":
            prev, gid_prev = None, r.GAME_ID
            if r.ACTION_TYPE == "period":
                continue
        end = None
        if r.ACTION_TYPE == "Made Shot":
            end = "make"
        elif r.ACTION_TYPE == "Turnover":
            d = str(r.DESCRIPTION).lower()
            end = "live_to" if any(k in d for k in LIVE) else "dead_to"
        elif r.ACTION_TYPE == "Rebound":
            end = "rebound"
        if end is None:
            continue
        if prev is not None and not pd.isna(r.SEC_REMAINING) and not pd.isna(r.TEAM_ID):
            pk, pt, pt_t, pp = prev
            # the team that now ends a possession is the one that had the ball
            if pt != r.TEAM_ID or pk == "rebound":
                dur = pt_t - r.SEC_REMAINING
                if 0 < dur <= 30:
                    rows.append({"GAME_ID": r.GAME_ID, "PERIOD": r.PERIOD,
                                 "mid": elapsed(r.PERIOD, (pt_t + r.SEC_REMAINING) / 2),
                                 "off_team": r.TEAM_ID, "dur": dur, "start": pk})
        prev = (end, r.TEAM_ID, r.SEC_REMAINING, r.PERIOD)
    return pd.DataFrame(rows)


def lineup_index(season):
    f = STINTS / f"{season}.parquet"
    df = pd.read_parquet(f)
    df = df[df.SEASON_TYPE == "Regular Season"]
    out = defaultdict(list)
    for r in df.itertuples():
        out[r.GAME_ID].append((r.START_SEC, r.END_SEC, r.HOME_TEAM_ID,
                               tuple(r.HOME_LINEUP), tuple(r.AWAY_LINEUP)))
    for g in out:
        out[g].sort()
    return out


def attach(poss, lu):
    off5, def5 = [], []
    for r in poss.itertuples():
        five_o = five_d = None
        for s, e, htid, h5, a5 in lu.get(r.GAME_ID, ()):
            if s <= r.mid < e:
                if r.off_team == htid:
                    five_o, five_d = h5, a5
                else:
                    five_o, five_d = a5, h5
                break
        off5.append(five_o)
        def5.append(five_d)
    poss = poss.copy()
    poss["off5"], poss["def5"] = off5, def5
    return poss.dropna(subset=["off5", "def5"])


def design(poss, players):
    idx = {p: i for i, p in enumerate(players)}
    n = len(players)
    ro, co, vo, rd, cd, vd = [], [], [], [], [], []
    for i, r in enumerate(poss.itertuples()):
        for p in r.off5:
            if p in idx:
                ro.append(i); co.append(idx[p]); vo.append(1.0)
        for p in r.def5:
            if p in idx:
                rd.append(i); cd.append(idx[p]); vd.append(1.0)
    m = len(poss)
    O = csr_matrix((vo, (ro, co)), shape=(m, n))
    D = csr_matrix((vd, (rd, cd)), shape=(m, n))
    S = csr_matrix((np.ones(m), (np.arange(m),
                    poss.start.map({s: i for i, s in enumerate(STARTS)}).fillna(0).astype(int))),
                   shape=(m, len(STARTS)))
    return hstack([S, O, D]).tocsr()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-poss", type=int, default=600)
    args = ap.parse_args()

    tr = []
    for s in TRAIN:
        p = attach(possessions(s), lineup_index(s))
        p["SEASON"] = s
        tr.append(p)
        print(f"  {s}: {len(p):,} possessions with a lineup", flush=True)
    tr = pd.concat(tr, ignore_index=True)

    cnt = defaultdict(int)
    for r in tr.itertuples():
        for p in r.off5:
            cnt[p] += 1
    players = sorted([p for p, c in cnt.items() if c >= args.min_poss])
    print(f"players with >={args.min_poss} offensive possessions: {len(players):,}")

    X = design(tr, players)
    y = tr.dur.to_numpy()
    rng = np.random.default_rng(0)
    m = rng.random(X.shape[0]) < 0.8
    best = None
    for lam in LAMBDAS:
        b = lsqr(X[m], y[m], damp=np.sqrt(lam), iter_lim=300)[0]
        e = np.sqrt(np.mean((y[~m] - X[~m] @ b) ** 2))
        if best is None or e < best[1]:
            best = (lam, e)
    lam = best[0]
    beta = lsqr(X, y, damp=np.sqrt(lam), iter_lim=400)[0]
    ns = len(STARTS)
    print(f"  ridge penalty {lam} (held-out rmse {best[1]:.3f})")
    print(f"  start-type effects: " +
          "  ".join(f"{s} {beta[i]:.2f}s" for i, s in enumerate(STARTS)))
    off = dict(zip(players, beta[ns:ns + len(players)]))
    dfn = dict(zip(players, beta[ns + len(players):]))

    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "PLAYER"])
    nm = {r.PLAYER_ID: r.PLAYER for r in ps[ps.SEASON == TRAIN[-1]].itertuples()}
    res = pd.DataFrame({"PLAYER_ID": players,
                        "OFF_SEC": [off[p] for p in players],
                        "DEF_SEC": [dfn[p] for p in players]})
    res["PLAYER"] = [nm.get(p, "?") for p in res.PLAYER_ID]
    res.to_parquet(OUT, index=False)

    print("\nSPEEDS UP his own offence (negative = shorter possessions):")
    for r in res.nsmallest(6, "OFF_SEC").itertuples():
        print(f"  {r.PLAYER:<24}{r.OFF_SEC:+.2f}s")
    print("SLOWS his own offence:")
    for r in res.nlargest(6, "OFF_SEC").itertuples():
        print(f"  {r.PLAYER:<24}{r.OFF_SEC:+.2f}s")
    print("\nForces the OPPONENT to take longer (defensive pressure):")
    for r in res.nlargest(5, "DEF_SEC").itertuples():
        print(f"  {r.PLAYER:<24}{r.DEF_SEC:+.2f}s")

    # ---- held-out validation ----
    te = attach(possessions(TEST), lineup_index(TEST))
    tmean = tr.dur.mean()
    start_fx = {s: beta[i] for i, s in enumerate(STARTS)}
    base_start = te.start.map(start_fx).fillna(tmean).to_numpy()
    pl = np.array([sum(off.get(p, 0.0) for p in r.off5)
                   + sum(dfn.get(p, 0.0) for p in r.def5) for r in te.itertuples()])
    act = te.dur.to_numpy()
    print(f"\nHeld-out {TEST}: {len(te):,} possessions")
    print(f"  {'model':<40}{'MAE':>9}")
    print(f"  {'grand mean only':<40}{np.abs(act - tmean).mean():>9.4f}")
    print(f"  {'start-type effects only':<40}{np.abs(act - base_start).mean():>9.4f}")
    for k in (0.0, 0.5, 1.0):
        pred = base_start + k * pl
        print(f"  {'+ ' + str(k) + ' x player pace effects':<40}"
              f"{np.abs(act - pred).mean():>9.4f}")

    # ---- is tempo a TEAM property or a LINEUP property? ----
    # This project's recurring lesson is that aggregates beat granularity. Tempo
    # is the exception, and it is worth showing rather than asserting: team-level
    # tendencies wash out because a team's starters and bench play at very
    # different speeds, so the team mean sits between them and describes neither.
    # CRITICAL: team residuals must be measured against the SAME baseline the
    # player model uses (the ridge start-type coefficients), not against raw group
    # means. Mixing the two put the comparison on different intercepts and made
    # "+ opponent tempo" look worse than predicting nothing.
    tr["resid"] = tr.dur - tr.start.map(start_fx).fillna(tmean)
    off_t = tr.groupby("off_team").resid.mean().to_dict()
    g = pd.read_parquet(ROOT / "data" / "parquet" / "games.parquet",
                        columns=["GAME_ID", "HOME_TEAM_ID", "AWAY_TEAM_ID"])
    ha = {r.GAME_ID: (r.HOME_TEAM_ID, r.AWAY_TEAM_ID) for r in g.itertuples()}

    def dteam(df):
        return [None if r.GAME_ID not in ha else
                (ha[r.GAME_ID][1] if r.off_team == ha[r.GAME_ID][0] else ha[r.GAME_ID][0])
                for r in df.itertuples()]
    tr2 = tr.assign(def_team=dteam(tr)).dropna(subset=["def_team"])
    def_t = tr2.groupby("def_team").resid.mean().to_dict()
    te2 = te.assign(def_team=dteam(te)).dropna(subset=["def_team"])
    b2 = te2.start.map(start_fx).fillna(tmean).to_numpy()
    o2 = te2.off_team.map(off_t).fillna(0.0).to_numpy()
    d2 = te2.def_team.map(def_t).fillna(0.0).to_numpy()
    a2 = te2.dur.to_numpy()
    print(f"\n  TEAM-level tempo, same held-out season ({len(te2):,} possessions):")
    print(f"  {'start-type only':<40}{np.abs(a2 - b2).mean():>9.4f}")
    print(f"  {'+ own team tempo':<40}{np.abs(a2 - b2 - o2).mean():>9.4f}")
    print(f"  {'+ own + opponent team tempo':<40}{np.abs(a2 - b2 - o2 - d2).mean():>9.4f}")
    # centre both team effects so they add to the ridge baseline coherently
    mo, md = float(np.mean(list(off_t.values()))), float(np.mean(list(def_t.values())))
    o2c, d2c = o2 - mo, d2 - md
    print(f"  {'+ own team tempo (centred)':<40}{np.abs(a2 - b2 - o2c).mean():>9.4f}")
    print(f"  {'+ own + opponent team tempo (centred)':<40}"
          f"{np.abs(a2 - b2 - o2c - d2c).mean():>9.4f}")
    print(f"  team spread: own {np.std(list(off_t.values())):.3f}s  "
          f"opp {np.std(list(def_t.values())):.3f}s")


if __name__ == "__main__":
    main()
