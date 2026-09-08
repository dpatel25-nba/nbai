"""
What should a player be regressed TOWARD?

`marcel` in script 124 shrinks every projection toward a single league mean:
the minutes-weighted average of all prior seasons, one number per statistic for
every player in the league. The amount of shrinkage was tuned per metric in
script 126 and is not obviously wrong. What a player is shrunk toward has never
been examined, and it is the other half of the same estimator.

It matters most exactly where the simulator was found to be weakest. A centre
projecting 10 defensive rebounds per 36 is regressed toward a league mean near
4.7 — a number built mostly from guards, who are not drawn from his
distribution at all. Shrinking him 15% toward 4.7 costs 0.8 rebounds; toward a
centre mean near 7.5 it costs half that. The same argument applies to assists
for guards and blocks for bigs, in the opposite direction for the players the
current prior flatters.

Empirical Bayes says the prior should be the distribution the player belongs to.
Position is a crude proxy for that, so this is a test rather than an assumption:
both priors are built from prior seasons only and scored on later ones, with a
cluster bootstrap over players because a player recurs across seasons and
unclustered intervals would be far too narrow.

Usage: python scripts/160_prior_by_position.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
N_BOOT = 2000
MIN_MIN = 400


def load_sim():
    spec = importlib.util.spec_from_file_location(
        "sim", ROOT / "scripts" / "124_possession_sim.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def marcel_prior(df, metric, K, recency, by=None):
    """Marcel with the prior taken either league-wide or within a group."""
    order = {s: i for i, s in enumerate(sorted(df.SEASON.unique()))}
    inv = {i: s for s, i in order.items()}
    val = {(r.PLAYER_ID, r.SEASON): getattr(r, metric) for r in df.itertuples()}
    wt = {(r.PLAYER_ID, r.SEASON): r.MIN for r in df.itertuples()}
    grp = ({int(r.PLAYER_ID): getattr(r, by) for r in df.itertuples()}
           if by else {})
    out, cache = {}, {}
    for r in df.itertuples():
        ti = order[r.SEASON]
        if ti == 0:
            continue
        key = (ti, grp.get(int(r.PLAYER_ID)) if by else None)
        if key not in cache:
            pri = df[df.SEASON.map(order) < ti]
            if by:
                pri = pri[pri[by] == key[1]]
            ok = pri[metric].notna() & pri.MIN.notna()
            cache[key] = (np.average(pri.loc[ok, metric], weights=pri.loc[ok, "MIN"])
                          if ok.sum() >= 30 else np.nan)
        pm = cache[key]
        if np.isnan(pm):                       # thin group -> league prior
            k2 = (ti, None)
            if k2 not in cache:
                pri = df[df.SEASON.map(order) < ti]
                ok = pri[metric].notna() & pri.MIN.notna()
                cache[k2] = (np.average(pri.loc[ok, metric],
                                        weights=pri.loc[ok, "MIN"])
                             if ok.any() else np.nan)
            pm = cache[k2]
        num = den = 0.0
        for lag, w in recency.items():
            s = inv.get(ti - lag)
            v, m = val.get((r.PLAYER_ID, s)), wt.get((r.PLAYER_ID, s))
            if v is not None and m is not None and not np.isnan(v):
                num += w * m * v
                den += w * m
        if den and not np.isnan(pm):
            out[(r.PLAYER_ID, r.SEASON)] = (num + K * pm) / (den + K)
    return out


def main() -> None:
    S = load_sim()
    ps = pd.read_parquet(PS)
    ps = ps[ps.MIN.notna()]
    # Group by HEIGHT, not by the POS label. POS is blank on 3,841 of 6,942
    # player-seasons — 55% — so a first attempt swept every unlabelled player
    # into one bucket and the grouping was mostly noise. That produced a null
    # for rebounds, the exact place the idea was aimed at, and the null was an
    # artefact of the grouping variable rather than a finding. Height is
    # objective, complete, and a better proxy for the distribution a rebounder
    # is drawn from than a positional label ever was.
    bio = pd.read_parquet(ROOT / "data/parquet/player_bio.parquet",
                          columns=["PLAYER_ID", "HEIGHT_IN"])
    h = {int(r.PLAYER_ID): float(r.HEIGHT_IN) for r in bio.itertuples()
         if r.HEIGHT_IN == r.HEIGHT_IN}
    def fam(pid):
        v = h.get(int(pid))
        if v is None:
            return "unknown"
        if v >= 82:
            return "6-10+"
        if v >= 79:
            return "6-7/6-9"
        if v >= 76:
            return "6-4/6-6"
        return "under 6-4"
    ps["fam"] = [fam(p) for p in ps.PLAYER_ID]
    ks = S.shrink_constants() if hasattr(S, "shrink_constants") else {}
    seasons = sorted(ps.SEASON.unique())
    cut = seasons[len(seasons) * 2 // 3]
    print(f"prior seasons < {cut}, scored on >= {cut}   "
          f"({ps.fam.value_counts().to_dict()})\n")
    print(f"  {'metric':<10}{'K':>7}{'league':>10}{'by position':>13}"
          f"{'change':>9}{'z':>7}{'95% CI':>18}")
    for metric, K in [("DREB_36", 400.0), ("OREB_36", 200.0), ("REB_36", 200.0),
                      ("AST_36", 400.0), ("BLK_36", 400.0), ("STL_36", 400.0),
                      ("FG3A_36", 50.0), ("PTS_36", 1000.0), ("TOV_36", 1000.0)]:
        if metric not in ps.columns:
            continue
        d = ps[ps[metric].notna()]
        a = marcel_prior(d, metric, K, S.RECENCY, by=None)
        b = marcel_prior(d, metric, K, S.RECENCY, by="fam")
        late = d[(d.SEASON >= cut) & (d.MIN >= MIN_MIN)]
        rows = []
        for r in late.itertuples():
            k = (r.PLAYER_ID, r.SEASON)
            if k in a and k in b:
                y = getattr(r, metric)
                rows.append((int(r.PLAYER_ID), abs(y - a[k]), abs(y - b[k])))
        if len(rows) < 200:
            continue
        pid = np.array([x[0] for x in rows])
        ea = np.array([x[1] for x in rows])
        eb = np.array([x[2] for x in rows])
        uniq, inv2 = np.unique(pid, return_inverse=True)
        idx = [np.flatnonzero(inv2 == i) for i in range(len(uniq))]
        rng = np.random.default_rng(7)
        boot = np.empty(N_BOOT)
        for i in range(N_BOOT):
            sel = np.concatenate([idx[j] for j in
                                  rng.integers(0, len(uniq), len(uniq))])
            boot[i] = (eb[sel].mean() / ea[sel].mean() - 1) * 100
        ch = (eb.mean() / ea.mean() - 1) * 100
        lo, hi = np.percentile(boot, [2.5, 97.5])
        print(f"  {metric:<10}{K:>7.0f}{ea.mean():>10.4f}{eb.mean():>13.4f}"
              f"{ch:>8.2f}%{ch/max(boot.std(ddof=1),1e-9):>7.1f}"
              f"{f'[{lo:+.2f},{hi:+.2f}]':>18}")
    print("\n  negative change = the position prior projects better")
    print("  REAL only where the interval lies entirely below zero")


if __name__ == "__main__":
    main()
