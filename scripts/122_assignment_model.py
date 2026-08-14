"""
Defensive assignment model — WHO GUARDS WHOM.

Given the two fives the rotation engine puts on the floor, the possession engine
needs to know which defender is matched to the ball-handler. That is what the
boxscorematchupsv3 feed measures directly (partial possessions per defender-
offender pair), and it now covers 2017-18 through 2025-26.

The honest question this script answers is NOT "does matchup data predict
scoring" — your studies falsified that three ways (script 88: even the idealized
version added zero out-of-sample points signal; script 100: bottom-up play-type
lost to top-down aggregate). It is the narrower, generative question the
simulator actually needs:

    given both players are on the floor together, how are an offensive player's
    possessions DISTRIBUTED across the five defenders?

Two competing explanations are tested head-to-head:

  AVAILABILITY  share(d|o) proportional to time d and o spend on court together.
                A pure-opportunity null — no notion of position at all.
  POSITIONAL    share(d|o) proportional to co-time x A[pos(d)][pos(o)], where the
                affinity matrix A is estimated from the data.

If POSITIONAL beats AVAILABILITY out-of-sample, matchups are structured enough to
matter for a simulator's assignment step, and A is what the engine uses. If not,
availability weighting is the honest default and we say so.

Co-on-court time comes from the reconstructed stints (script 120), which is the
denominator that makes the share conditional rather than confounded by minutes.

Fit on 2017-18…2024-25, tested out-of-sample on 2025-26.

Output: data/parquet/assignment_affinity.parquet
Usage: python scripts/122_assignment_model.py
"""

from __future__ import annotations

import glob
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STINTS = ROOT / "data" / "parquet" / "stints"
MU = ROOT / "data" / "parquet" / "matchups.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
OUT = ROOT / "data" / "parquet" / "assignment_affinity.parquet"
BIO = ROOT / "data" / "parquet" / "player_bio.parquet"

FIRST = "2017-18"          # matchup coverage begins here
TEST = "2025-26"
POSITIONS = ["G", "F", "C"]


def pos_bucket(p) -> str:
    if not isinstance(p, str):
        return "F"
    for k in ("C", "G", "F"):      # centre first: "F-C" is a big
        if k in p:
            return k
    return "F"


def co_oncourt_seconds(seasons: list[str]) -> dict:
    """(GAME_ID, def_player, off_player) -> seconds both were on the floor."""
    co: dict = defaultdict(float)
    for s in seasons:
        f = STINTS / f"{s}.parquet"
        if not f.exists():
            continue
        df = pd.read_parquet(f)
        df = df[df.SEASON_TYPE == "Regular Season"]
        for r in df.itertuples():
            d = r.DUR_SEC
            for h in r.HOME_LINEUP:
                for a in r.AWAY_LINEUP:
                    co[(r.GAME_ID, h, a)] += d      # h guards a
                    co[(r.GAME_ID, a, h)] += d      # and a guards h
        print(f"  co-on-court {s}: {len(co):,} pairs so far", flush=True)
    return co


def assemble(seasons: list[str], co: dict) -> pd.DataFrame:
    mu = pd.read_parquet(MU, columns=["GAME_ID", "SEASON", "SEASON_TYPE",
                                      "DEF_ID", "OFF_ID", "partial_poss"])
    mu = mu[(mu.SEASON_TYPE == "Regular Season") & mu.SEASON.isin(seasons)]
    mu = mu.groupby(["GAME_ID", "SEASON", "DEF_ID", "OFF_ID"], as_index=False).partial_poss.sum()
    mu["co_sec"] = [co.get((g, d, o), 0.0) for g, d, o in
                    zip(mu.GAME_ID, mu.DEF_ID, mu.OFF_ID)]
    # a pair with no reconstructed shared floor time can't inform a conditional share
    return mu[mu.co_sec > 0].copy()


def add_positions(df: pd.DataFrame) -> pd.DataFrame:
    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "POS"])
    pos = {(r.PLAYER_ID, r.SEASON): pos_bucket(r.POS) for r in ps.itertuples()}
    df["dpos"] = [pos.get((d, s), "F") for d, s in zip(df.DEF_ID, df.SEASON)]
    df["opos"] = [pos.get((o, s), "F") for o, s in zip(df.OFF_ID, df.SEASON)]
    return df


def shares(df: pd.DataFrame) -> pd.DataFrame:
    """Actual and availability-expected share of each offender's guarded poss."""
    g = df.groupby(["GAME_ID", "OFF_ID"])
    df["tot_poss"] = g.partial_poss.transform("sum")
    df["tot_co"] = g.co_sec.transform("sum")
    df = df[(df.tot_poss > 0) & (df.tot_co > 0)].copy()
    df["actual"] = df.partial_poss / df.tot_poss
    df["avail"] = df.co_sec / df.tot_co
    return df


def fit_affinity(df: pd.DataFrame) -> np.ndarray:
    """A[dpos][opos] = how much more a pos-pair is guarded than availability implies."""
    A = np.ones((3, 3))
    pi = {p: i for i, p in enumerate(POSITIONS)}
    di = df.dpos.map(pi).to_numpy()
    oi = df.opos.map(pi).to_numpy()
    act = df.actual.to_numpy()
    av = df.avail.to_numpy()
    for _ in range(50):                       # iterative proportional fitting
        w = av * A[di, oi]
        # renormalize within each offender so the weights are a share
        s = pd.Series(w).groupby([df.GAME_ID.values, df.OFF_ID.values]).transform("sum").to_numpy()
        pred = np.where(s > 0, w / s, 0.0)
        newA = A.copy()
        for a in range(3):
            for b in range(3):
                m = (di == a) & (oi == b)
                if m.sum() > 50 and pred[m].sum() > 0:
                    newA[a, b] = A[a, b] * (act[m].sum() / pred[m].sum())
        newA /= newA.mean()
        if np.abs(newA - A).max() < 1e-4:
            A = newA
            break
        A = newA
    return A


def evaluate(df: pd.DataFrame, A: np.ndarray, label: str):
    pi = {p: i for i, p in enumerate(POSITIONS)}
    di = df.dpos.map(pi).to_numpy()
    oi = df.opos.map(pi).to_numpy()
    w = df.avail.to_numpy() * A[di, oi]
    s = pd.Series(w).groupby([df.GAME_ID.values, df.OFF_ID.values]).transform("sum").to_numpy()
    pred_pos = np.where(s > 0, w / s, 0.0)
    act = df.actual.to_numpy()
    n_def = df.groupby(["GAME_ID", "OFF_ID"]).DEF_ID.transform("size").to_numpy()
    unif = 1.0 / n_def
    print(f"\n{label} — {len(df):,} defender-offender-game rows, "
          f"{df.groupby(['GAME_ID','OFF_ID']).ngroups:,} offender-games")
    print(f"  {'model':<34}{'share MAE':>11}{'corr':>9}")
    for nm, p in [("uniform over co-present defenders", unif),
                  ("AVAILABILITY (co-on-court time)", df.avail.to_numpy()),
                  ("POSITIONAL (co-time x affinity)", pred_pos)]:
        print(f"  {nm:<34}{np.abs(p - act).mean():>11.4f}{np.corrcoef(p, act)[0,1]:>9.3f}")
    return pred_pos


def height_test(train, test, A):
    """Does actual height beat G/F/C buckets at predicting who guards whom?

    Positions are a three-level proxy for size. If defenders genuinely match up
    by height, the height GAP between a defender and the man he is guarding
    should predict assignment share beyond what position already captures.
    """
    if not BIO.exists():
        print("\n(no bio data yet — skipping the height test)")
        return
    bio = pd.read_parquet(BIO)
    h = {int(r.PLAYER_ID): float(r.HEIGHT_IN) for r in bio.itertuples()
         if pd.notna(r.HEIGHT_IN)}
    pi = {p: i for i, p in enumerate(POSITIONS)}

    def prep(d):
        d = d.copy()
        d["dh"] = d.DEF_ID.map(h)
        d["oh"] = d.OFF_ID.map(h)
        return d.dropna(subset=["dh", "oh"])
    tr, te = prep(train), prep(test)
    cov = len(te) / max(len(test), 1)
    print(f"\n=== HEIGHT vs POSITION in the assignment model ===")
    print(f"  bio coverage of test rows: {cov*100:.0f}%  ({len(te):,} of {len(test):,})")
    if len(te) < 5000:
        print("  too few covered rows to judge")
        return
    tr["gap"] = tr.dh - tr.oh
    te["gap"] = te.dh - te.oh
    # empirical: how does the height gap relate to being the assigned defender?
    tr["b"] = pd.cut(tr.gap, [-99, -4, -2, 2, 4, 99],
                     labels=["4+ shorter", "2-4 shorter", "within 2", "2-4 taller", "4+ taller"])
    g = tr.groupby("b", observed=True).apply(
        lambda d: (d.actual.sum() / d.avail.sum()) if d.avail.sum() > 0 else np.nan)
    print(f"\n  {'height gap (def - off)':<24}{'share vs availability':>22}")
    for k, v in g.items():
        print(f"  {str(k):<24}{v:>22.3f}")
    # fit a multiplicative height term on top of the positional model
    lift = {k: (v if np.isfinite(v) else 1.0) for k, v in g.items()}
    di = te.dpos.map(pi).to_numpy(); oi = te.opos.map(pi).to_numpy()
    base = te.avail.to_numpy() * A[di, oi]
    hb = pd.cut(te.gap, [-99, -4, -2, 2, 4, 99],
                labels=["4+ shorter", "2-4 shorter", "within 2", "2-4 taller", "4+ taller"])
    hmul = hb.map(lift).astype(float).fillna(1.0).to_numpy()
    act = te.actual.to_numpy()

    # WEIGHT — does mass add anything once height is accounted for? Height and
    # weight correlate strongly, so a coarse bucketed control leaves height
    # inside the weight term. The honest test is whether adding weight improves
    # out-of-sample share prediction on top of height, which is exactly the bar
    # height itself had to clear.
    wt = {int(r.PLAYER_ID): float(r.WEIGHT_LB) for r in bio.itertuples()
          if pd.notna(r.WEIGHT_LB)}
    for d in (tr, te):
        d["wgap"] = d.DEF_ID.map(wt) - d.OFF_ID.map(wt)
    wbins = [-999, -30, -10, 10, 30, 999]
    wlab = ["30+ light", "10-30 light", "within 10", "10-30 heavy", "30+ heavy"]
    trw = tr.dropna(subset=["wgap"]).copy()
    trw["hb"] = pd.cut(trw.gap, [-99, -4, -2, 2, 4, 99],
                       labels=["4+ shorter", "2-4 shorter", "within 2",
                               "2-4 taller", "4+ taller"])
    trw["hmul"] = trw.hb.map(lift).astype(float).fillna(1.0)
    trw["wb"] = pd.cut(trw.wgap, wbins, labels=wlab)
    # weight lift measured on top of availability x position x height
    wlift = trw.groupby("wb", observed=True).apply(
        lambda d: (d.actual.sum() / (d.avail * d.hmul).sum())
        if (d.avail * d.hmul).sum() > 0 else 1.0, include_groups=False)
    print("\n  weight-gap lift, measured ON TOP of height:")
    for k, v in wlift.items():
        print(f"    {str(k):<16}{v:>8.3f}")
    wmul = (pd.cut(te.wgap, wbins, labels=wlab).map(wlift)
            .astype(float).fillna(1.0).to_numpy())
    print(f"\n  {'model':<40}{'share MAE':>11}{'corr':>9}")
    for nm, w in (("availability only", te.avail.to_numpy()),
                  ("+ position affinity", base),
                  ("+ position + height gap", base * hmul),
                  ("+ position + height + weight", base * hmul * wmul)):
        ssum = pd.Series(w).groupby([te.GAME_ID.values, te.OFF_ID.values]).transform("sum").to_numpy()
        pred = np.where(ssum > 0, w / ssum, 0.0)
        print(f"  {nm:<40}{np.abs(pred-act).mean():>11.4f}{np.corrcoef(pred,act)[0,1]:>9.3f}")


def main() -> None:
    seasons = [s for s in sorted(p.stem for p in STINTS.glob("*.parquet")) if s >= FIRST]
    train_s = [s for s in seasons if s != TEST]
    print(f"Seasons: train {train_s[0]}…{train_s[-1]}  test {TEST}")

    print("\nComputing co-on-court time from stints...")
    co = co_oncourt_seconds(seasons)

    df = add_positions(shares(assemble(seasons, co)))
    train, test = df[df.SEASON != TEST], df[df.SEASON == TEST]
    print(f"\nRows: train {len(train):,} | test {len(test):,}")

    A = fit_affinity(train)
    print("\nPositional affinity A[defender][offender]  (1.0 = exactly availability):")
    print(f"  {'':<8}" + "".join(f"{'vs '+p:>10}" for p in POSITIONS))
    for i, p in enumerate(POSITIONS):
        print(f"  {p:<8}" + "".join(f"{A[i, j]:>10.3f}" for j in range(3)))

    evaluate(train, A, "IN-SAMPLE (train)")
    evaluate(test, A, f"OUT-OF-SAMPLE ({TEST})")

    height_test(train, test, A)

    pd.DataFrame(A, index=POSITIONS, columns=POSITIONS).reset_index().rename(
        columns={"index": "dpos"}).to_parquet(OUT, index=False)
    print(f"\nSaved affinity -> {OUT}")


if __name__ == "__main__":
    main()
