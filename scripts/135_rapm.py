"""
RAPM from reconstructed stints — impact and tempo, both opponent-adjusted.

This is the keystone the north-star notes have wanted from the start, and it is
only possible now that script 120 rebuilt who was on the floor at every moment.
It buys two things the simulator currently lacks:

 1. AN INDEPENDENT BASIS FOR THE COUNTERFACTUALS. The "what if Jokic sits" path
    runs on BPM3, which cancels out of every backtest and therefore has never
    been validated against anything. RAPM is estimated from on-court margin
    directly, so it can be checked against BPM3 on a real out-of-sample task.

 2. A CLEAN PLAYER PACE EFFECT. Raw on-court pace is confounded with team — the
    five "fastest" players in 2024-25 were three Memphis teammates plus two
    centres on fast teams, which is one team effect wearing five hats. Regressing
    stint pace on player indicators separates a player's own tempo contribution
    from his teammates' and his opponents'.

Method, standard ridge RAPM:
    each stint contributes one row per offensive team
    X: +1 for the five on offence, -1 for the five on defence
    y: points per 100 possessions for that team in that stint
    weights: possessions (a 2-possession stint should not count like a 40-second one)
    ridge penalty chosen by held-out error, not by taste — raw APM is hopeless
    without it because lineups are collinear

Pace RAPM uses the same stints but +1 for ALL TEN players, since tempo is jointly
produced rather than contested, with y = possessions per 48 minutes.

Output: data/parquet/rapm.parquet
Usage: python scripts/135_rapm.py --seasons 2022-23,2023-24,2024-25
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import lsqr

ROOT = Path(__file__).resolve().parents[1]
STINTS = ROOT / "data" / "parquet" / "stints"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
WAR3 = ROOT / "data" / "parquet" / "player_seasons_war_v3.parquet"
TG = ROOT / "data" / "parquet" / "team_games.parquet"
OUT = ROOT / "data" / "parquet" / "rapm.parquet"

MIN_POSS = 2          # stints shorter than this carry no information
LAMBDAS = [500, 1000, 2000, 4000, 8000, 16000, 32000]


def load_stints(seasons):
    fr = []
    for s in seasons:
        f = STINTS / f"{s}.parquet"
        if f.exists():
            d = pd.read_parquet(f)
            fr.append(d[d.SEASON_TYPE == "Regular Season"])
    return pd.concat(fr, ignore_index=True)


def build_design(df, pace_mode=False):
    """-> (X, y, w, players). pace_mode uses all ten players and stint tempo."""
    players = sorted({p for L in df.HOME_LINEUP for p in L}
                     | {p for L in df.AWAY_LINEUP for p in L})
    idx = {p: i for i, p in enumerate(players)}
    n = len(players)
    rows, cols, vals, y, w = [], [], [], [], []
    r = 0
    for t in df.itertuples():
        if pace_mode:
            if t.DUR_SEC < 30:
                continue
            for p in list(t.HOME_LINEUP) + list(t.AWAY_LINEUP):
                rows.append(r); cols.append(idx[p]); vals.append(1.0)
            y.append((t.HOME_POSS + t.AWAY_POSS) / 2.0 / t.DUR_SEC * 2880.0)
            w.append(t.DUR_SEC)
            r += 1
            continue
        for off, dfn, pts, poss in ((t.HOME_LINEUP, t.AWAY_LINEUP, t.HOME_PTS, t.HOME_POSS),
                                    (t.AWAY_LINEUP, t.HOME_LINEUP, t.AWAY_PTS, t.AWAY_POSS)):
            if poss < MIN_POSS:
                continue
            for p in off:
                rows.append(r); cols.append(idx[p]); vals.append(1.0)
            for p in dfn:
                rows.append(r); cols.append(idx[p]); vals.append(-1.0)
            y.append(pts / poss * 100.0)
            w.append(poss)
            r += 1
    X = csr_matrix((vals, (rows, cols)), shape=(r, n))
    return X, np.array(y), np.array(w), players


def ridge(X, y, w, lam):
    """Weighted ridge via lsqr; damp=sqrt(lam) minimises ||Ax-b||^2 + lam||x||^2."""
    sw = np.sqrt(w)
    Xw = X.multiply(sw[:, None]).tocsr()
    yw = y * sw
    yw = yw - yw.mean()
    return lsqr(Xw, yw, damp=np.sqrt(lam), atol=1e-8, btol=1e-8, iter_lim=400)[0]


def pick_lambda(X, y, w, seed=0):
    """Hold out a random 20% of stints and score each penalty on it."""
    rng = np.random.default_rng(seed)
    m = rng.random(X.shape[0]) < 0.8
    best, bl = None, None
    for lam in LAMBDAS:
        b = ridge(X[m], y[m], w[m], lam)
        pred = X[~m] @ b
        err = np.sqrt(np.average((y[~m] - pred - (y[m].mean() - 0)) ** 2, weights=w[~m]))
        if best is None or err < best:
            best, bl = err, lam
    return bl, best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2022-23,2023-24,2024-25")
    args = ap.parse_args()
    seasons = args.seasons.split(",")
    df = load_stints(seasons)
    print(f"stints: {len(df):,} across {df.GAME_ID.nunique():,} games "
          f"({seasons[0]}…{seasons[-1]})")

    # ---- impact RAPM ----
    X, y, w, players = build_design(df)
    print(f"design: {X.shape[0]:,} rows x {X.shape[1]:,} players")
    lam, err = pick_lambda(X, y, w)
    print(f"ridge penalty chosen on held-out stints: lambda={lam}  (rmse {err:.2f})")
    beta = ridge(X, y, w, lam)

    # ---- pace RAPM ----
    Xp, yp, wp, players_p = build_design(df, pace_mode=True)
    lam_p, err_p = pick_lambda(Xp, yp, wp)
    beta_p = ridge(Xp, yp, wp, lam_p)
    pace_of = dict(zip(players_p, beta_p))
    print(f"pace design: {Xp.shape[0]:,} rows   lambda={lam_p}")

    mins = defaultdict(float)
    for t in df.itertuples():
        for p in list(t.HOME_LINEUP) + list(t.AWAY_LINEUP):
            mins[p] += t.DUR_SEC / 60.0
    out = pd.DataFrame({"PLAYER_ID": players, "RAPM": beta,
                        "PACE_RAPM": [pace_of.get(p, 0.0) for p in players],
                        "MIN": [mins[p] for p in players]})
    out["SEASONS"] = ",".join(seasons)
    out.to_parquet(OUT, index=False)

    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "PLAYER", "TEAM"])
    ps = ps[ps.SEASON == seasons[-1]]
    nm = {r.PLAYER_ID: (r.PLAYER, r.TEAM) for r in ps.itertuples()}
    q = out[out.MIN >= 2000].copy()
    q["PLAYER"] = [nm.get(p, ("?", "?"))[0] for p in q.PLAYER_ID]
    print(f"\nplayers with >=2000 stint-minutes: {len(q):,}")
    print("\nTop 10 by RAPM (pts/100 on court, opponent- and teammate-adjusted):")
    for r in q.nlargest(10, "RAPM").itertuples():
        print(f"  {r.PLAYER:<24}{r.RAPM:+6.2f}   pace {r.PACE_RAPM:+5.2f}")
    print("\nBottom 5:")
    for r in q.nsmallest(5, "RAPM").itertuples():
        print(f"  {r.PLAYER:<24}{r.RAPM:+6.2f}")
    print("\nPACE effect — fastest (own contribution, teammates controlled for):")
    for r in q.nlargest(6, "PACE_RAPM").itertuples():
        print(f"  {r.PLAYER:<24}{r.PACE_RAPM:+5.2f} poss/48")
    print("Slowest:")
    for r in q.nsmallest(4, "PACE_RAPM").itertuples():
        print(f"  {r.PLAYER:<24}{r.PACE_RAPM:+5.2f} poss/48")

    # ---- does RAPM beat BPM3 at predicting team rating out of sample? ----
    w3 = pd.read_parquet(WAR3, columns=["PLAYER_ID", "SEASON", "BPM3"])
    tg = pd.read_parquet(TG)
    tg = tg[tg.SEASON_TYPE == "Regular Season"]
    net = {(t, s): (g.offensiveRating - g.defensiveRating).mean()
           for (t, s), g in tg.groupby(["TEAM_TRICODE", "SEASON"])}
    nxt = {"2024-25": "2025-26"}.get(seasons[-1])
    if nxt:
        psn = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "TEAM", "MIN"])
        psn = psn[psn.SEASON == nxt]
        bpm = {r.PLAYER_ID: r.BPM3 for r in w3[w3.SEASON == seasons[-1]].itertuples()}
        rap = dict(zip(out.PLAYER_ID, out.RAPM))
        rows = []
        for (tri, s), g in psn.groupby(["TEAM", "SEASON"]):
            if (tri, s) not in net:
                continue
            recs = [(r.MIN, bpm.get(r.PLAYER_ID), rap.get(r.PLAYER_ID))
                    for r in g.itertuples()]
            recs = [(m, b, a) for m, b, a in recs if b is not None and a is not None]
            if len(recs) < 5:
                continue
            W = sum(m for m, _, _ in recs)
            rows.append({"net": net[(tri, s)],
                         "bpm": sum(m * b for m, b, _ in recs) / W,
                         "rapm": sum(m * a for m, _, a in recs) / W})
        d = pd.DataFrame(rows)
        if len(d) > 15:
            print(f"\nPredicting {nxt} team net rating from {seasons[-1]} player values "
                  f"({len(d)} teams):")
            for lbl, c in (("BPM3 (what the sim uses)", "bpm"), ("RAPM", "rapm")):
                print(f"  {lbl:<26} corr {np.corrcoef(d[c], d.net)[0,1]:+.3f}")
    print(f"\nSaved -> {OUT}")


if __name__ == "__main__":
    main()
