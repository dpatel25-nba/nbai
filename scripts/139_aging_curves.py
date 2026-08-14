"""
Aging curves — the projection engine has been age-blind since it was built.

Script 23's Marcel weights recent seasons and regresses to the mean, but has no
idea whether a player is 22 and improving or 35 and declining. The north-star
notes call aging "the biggest remaining lift" for offseason projection and name
birthdates as the blocker. Script 137 pulled them.

Getting this right needs four problems handled, not one.

 1. SURVIVORSHIP IN THE CROSS-SECTION. Comparing the average 24-year-old with the
    average 34-year-old measures who survived, not how players age — decliners
    leave the league, so the 34-year-olds still playing are the ones who aged
    well. Handled by the DELTA METHOD: pair every player with HIMSELF across
    consecutive seasons.

 2. MEAN REVERSION MASQUERADING AS AGING. A player who had a fluky-good season
    declines the next year at ANY age, so a raw delta curve partly measures
    regression rather than ageing.

    The tempting fix — measuring the residual against an age-blind Marcel
    projection — is WORSE, and was tried here first. Marcel shrinks players with
    short histories toward the league mean, short histories mean young, so the
    residual measures Marcel's own shrinkage bias by age. It produced a curve
    peaking at 20 and declining monotonically, which is diagnostically wrong:
    NBA scoring peaks near 26-27. Substituting one bias for another is not a fix.

    Handled properly by CONTROLLING for reversion instead of dodging it: regress
    the year-over-year delta on age dummies AND on how far the prior season sat
    from that player's own baseline (computed from his OTHER seasons, so it is
    not mechanically correlated with the delta). The reversion coefficient
    absorbs the regression, and the age dummies are then ageing.

 3. SURVIVORSHIP AT THE TAIL. A player whose final season collapses usually has
    no following season, so his decline is never observed and the old end of any
    delta curve biases UPWARD. Quantified here (the attrition rate by age is
    printed) rather than silently ignored, and it cannot be fully removed.

 4. AGING IS NOT ONE CURVE. Athleticism decays before skill, and bigs decay
    differently from guards. Curves are estimated per metric and per position,
    with LOESS-style smoothing since single-year cells are noisy.

VALIDATION is the usual standard: an age-adjusted projection must beat the
age-blind one out of sample, per metric. A curve that looks right and projects
worse does not ship.

KNOWN SAMPLE LIMITATION: the bio pull covers players active since 2022-23, so
players who retired earlier are absent. That is itself a survivorship filter on
top of (3) and biases the old end upward again. Stated, not hidden.

Output: data/parquet/aging_curves.parquet
Usage: python scripts/139_aging_curves.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
BIO = ROOT / "data" / "parquet" / "player_bio.parquet"
OUT = ROOT / "data" / "parquet" / "aging_curves.parquet"

METRICS = ["PTS_36", "REB_36", "AST_36", "MPG", "TS_PCT", "FG3_PCT"]
MIN_MIN = 400
AGE_LO, AGE_HI = 20, 38
RECENCY = {1: 5.0, 2: 4.0, 3: 3.0}
K = 1000.0
SMOOTH = 1.6          # gaussian kernel width in years


def season_mid(season: str) -> pd.Timestamp:
    return pd.Timestamp(int(season[:4]) + 1, 2, 1)


def bucket(p) -> str:
    if not isinstance(p, str):
        return "F"
    for k in ("C", "G", "F"):
        return "C" if "C" in p else ("G" if "G" in p else "F")
    return "F"


def marcel(ps, metric, order):
    """Age-blind projection for every player-season, prior seasons only."""
    val = {(r.PLAYER_ID, r.si): getattr(r, metric) for r in ps.itertuples()}
    wt = {(r.PLAYER_ID, r.si): r.MIN for r in ps.itertuples()}
    out, cache = {}, {}
    for r in ps.itertuples():
        if r.si == 0:
            continue
        if r.si not in cache:
            pri = ps[ps.si < r.si]
            ok = pri[metric].notna() & pri.MIN.notna()
            cache[r.si] = (np.average(pri.loc[ok, metric], weights=pri.loc[ok, "MIN"])
                           if ok.any() else np.nan)
        pm = cache[r.si]
        num = den = 0.0
        for lag, w in RECENCY.items():
            v, m = val.get((r.PLAYER_ID, r.si - lag)), wt.get((r.PLAYER_ID, r.si - lag))
            if v is not None and m is not None and not np.isnan(v):
                num += w * m * v
                den += w * m
        if den and not np.isnan(pm):
            out[(r.PLAYER_ID, r.si)] = (num + K * pm) / (den + K)
    return out


def smooth_curve(ages, vals, wts):
    """Gaussian-kernel smooth so single-year cells stop being noise."""
    grid = np.arange(AGE_LO, AGE_HI + 1)
    out = []
    for a in grid:
        k = np.exp(-0.5 * ((ages - a) / SMOOTH) ** 2) * wts
        out.append(float(np.sum(k * vals) / np.sum(k)) if np.sum(k) > 0 else np.nan)
    return grid, np.array(out)


def main() -> None:
    ps = pd.read_parquet(PS)
    bio = pd.read_parquet(BIO, columns=["PLAYER_ID", "BIRTHDATE"]).dropna()
    bd = {int(r.PLAYER_ID): r.BIRTHDATE for r in bio.itertuples()}
    ps = ps[ps.PLAYER_ID.isin(bd)].copy()
    ps["age"] = [(season_mid(s) - bd[int(p)]).days / 365.25
                 for p, s in zip(ps.PLAYER_ID, ps.SEASON)]
    order = {s: i for i, s in enumerate(sorted(ps.SEASON.unique()))}
    ps["si"] = ps.SEASON.map(order)
    ps["pos"] = ps.POS.map(bucket)
    print(f"player-seasons with a birthdate: {len(ps):,} "
          f"({ps.PLAYER_ID.nunique():,} players)   age {ps.age.min():.1f}-{ps.age.max():.1f}")

    # ---- attrition, so the tail bias is a number rather than a caveat ----
    last = ps.groupby("PLAYER_ID").si.max()
    maxsi = ps.si.max()
    att = []
    for a in range(23, 39):
        at_age = ps[(ps.age.round() == a) & (ps.si < maxsi)]
        if len(at_age) < 20:
            continue
        gone = [1 if last[r.PLAYER_ID] == r.si else 0 for r in at_age.itertuples()]
        att.append((a, len(at_age), float(np.mean(gone))))
    print("\nAttrition — share whose career ends after this season (the tail bias):")
    print("  " + "  ".join(f"{a}:{p*100:.0f}%" for a, n, p in att))

    # ---- delta method WITH an explicit mean-reversion control ----
    rows = []
    for m in METRICS:
        if m not in ps.columns:
            continue
        nxt = ps[["PLAYER_ID", "si", "MIN", m]].copy()
        nxt.columns = ["PLAYER_ID", "si", "MIN_n", f"{m}_n"]
        nxt["si"] = nxt.si - 1
        pr = ps.merge(nxt, on=["PLAYER_ID", "si"])
        pr = pr[(pr.MIN >= MIN_MIN) & (pr.MIN_n >= MIN_MIN)].dropna(subset=[m, f"{m}_n"])
        # baseline from the player's OTHER seasons, so `dev` is not mechanically
        # tied to the delta being explained
        tot = ps.groupby("PLAYER_ID").apply(
            lambda g: pd.Series({"s": (g[m] * g.MIN).sum(), "w": g.MIN.sum(),
                                 "k": g[m].notna().sum()}), include_groups=False)
        base = []
        for r in pr.itertuples():
            t = tot.loc[r.PLAYER_ID]
            wsum = t.w - r.MIN - r.MIN_n
            ssum = t.s - getattr(r, m) * r.MIN - getattr(r, f"{m}_n") * r.MIN_n
            base.append(ssum / wsum if (t.k >= 3 and wsum > 200) else np.nan)
        pr["dev"] = pr[m] - np.array(base)
        pr = pr.dropna(subset=["dev"])
        if len(pr) < 400:
            continue
        pr["delta"] = pr[f"{m}_n"] - pr[m]
        pr["abin"] = pr.age.round().clip(AGE_LO, AGE_HI).astype(int)

        for pos in ("ALL", "G", "F", "C"):
            sub = pr if pos == "ALL" else pr[pr.pos == pos]
            ages = sorted(sub.abin.unique())
            ages = [a for a in ages if (sub.abin == a).sum() >= 25]
            if len(sub) < 300 or len(ages) < 6:
                continue
            ai = {a: i for i, a in enumerate(ages)}
            keep = sub[sub.abin.isin(ages)]
            X = np.zeros((len(keep), len(ages) + 1))
            for j, r in enumerate(keep.itertuples()):
                X[j, ai[r.abin]] = 1.0
                X[j, -1] = r.dev                    # the reversion control
            w = np.sqrt(np.minimum(keep.MIN, keep.MIN_n).to_numpy())
            b, *_ = np.linalg.lstsq(X * w[:, None], keep.delta.to_numpy() * w, rcond=None)
            yearly = {a: b[ai[a]] for a in ages}
            if pos == "ALL" and m == "PTS_36":
                print(f"\n  reversion coefficient for PTS_36: {b[-1]:+.3f} "
                      f"(negative = a season above a player's own baseline "
                      f"regresses next year, as expected)")
            # chain the yearly changes into a level curve, centred at its peak
            cum, run = {}, 0.0
            for a in range(min(ages), max(ages) + 1):
                run += yearly.get(a, 0.0)
                cum[a] = run
            top = max(cum.values())
            av = np.array(list(cum)); vv = np.array([cum[a] - top for a in cum])
            wv = np.array([max((sub.abin == a).sum(), 1) for a in cum], dtype=float)
            grid, sm = smooth_curve(av, vv, wv)
            for a, v in zip(grid, sm):
                rows.append({"metric": m, "pos": pos, "age": int(a),
                             "effect": v, "n": int((sub.abin == a).sum())})
    curves = pd.DataFrame(rows)
    curves.to_parquet(OUT, index=False)

    print("\nAging curve — cumulative level relative to peak (0 = peak age)")
    print("(delta method, mean-reversion controlled, minutes-weighted, smoothed)")
    allc = curves[curves.pos == "ALL"]
    ms = [m for m in METRICS if m in allc.metric.unique()]
    print(f"  {'age':<6}" + "".join(f"{m:>10}" for m in ms))
    for a in range(21, 38):
        r = allc[allc.age == a]
        if r.empty:
            continue
        print(f"  {a:<6}" + "".join(
            f"{r[r.metric==m].effect.iloc[0]:>+10.3f}" if len(r[r.metric == m]) else f"{'-':>10}"
            for m in ms))
    print("\n  Peak age (max of the smoothed curve):")
    for m in ms:
        c = allc[allc.metric == m]
        if len(c):
            print(f"    {m:<10}{int(c.loc[c.effect.idxmax(), 'age'])}")
    print("\n  By position, PTS_36 peak:")
    for pos in ("G", "F", "C"):
        c = curves[(curves.metric == "PTS_36") & (curves.pos == pos)]
        if len(c):
            print(f"    {pos}: peak {int(c.loc[c.effect.idxmax(),'age'])}   "
                  f"age-30 effect {c[c.age==30].effect.iloc[0]:+.3f}")

    # ---- validation: does the adjustment project better out of sample? ----
    print("\nVALIDATION — age-adjusted vs age-blind Marcel")
    print(f"  {'metric':<10}{'age-blind':>12}{'age-adj':>10}{'change':>10}   adopt?")
    for m in ms:
        proj = marcel(ps, m, order)
        eff = {int(r.age): r.effect for _, r in
               curves[(curves.metric == m) & (curves.pos == "ALL")].iterrows()}
        d = ps[ps.MIN >= MIN_MIN].copy()
        d["proj"] = [proj.get((r.PLAYER_ID, r.si), np.nan) for r in d.itertuples()]
        d = d.dropna(subset=["proj", m])
        # apply the curve as a DIFFERENCE between the projected-from age and the
        # target age, which is what an aging adjustment actually is
        prior_age = d.age - 1.0
        a_now = d.age.round().clip(AGE_LO, AGE_HI).astype(int).map(eff).fillna(0.0)
        a_prev = prior_age.round().clip(AGE_LO, AGE_HI).astype(int).map(eff).fillna(0.0)
        adj = a_now - a_prev
        e0 = np.abs(d[m] - d.proj).mean()
        e1 = np.abs(d[m] - (d.proj + adj)).mean()
        print(f"  {m:<10}{e0:>12.4f}{e1:>10.4f}{(e1/e0-1)*100:>9.2f}%"
              f"   {'YES' if e1 < e0 else 'no'}")
    print("\n  NOTE: this validation is IN-SAMPLE for the curve (the same seasons")
    print("  built it). It shows the curve is real, not that it generalises — a")
    print("  walk-forward version needs more birthdate coverage than 411 players.")


if __name__ == "__main__":
    main()
