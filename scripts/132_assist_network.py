"""
Assist networks — who actually passes to whom.

The simulator credits an assist to a teammate weighted by that teammate's AST_36,
which treats a lineup as five interchangeable passers. Real offences do not work
that way: a point guard assists the roll man on a pick-and-roll far more often
than the other big does, and a corner shooter is fed by specific creators.

The pbp carries the passer only inside the description ("(Wallace 1 AST)"), the
same surname-resolution problem script 120 solved for substitutions, so the same
RosterIndex is reused. Combined with the reconstructed stints, which say who was
on the floor at that moment, that yields a genuine passer->scorer network.

The question is empirical and gets an honest test: given an assisted basket by a
known scorer with a known five on the floor, which teammate made the pass?

    baseline    weight teammates by AST_36                (what the sim does)
    network     weight by the pair's own historical rate, shrunk toward the
                passer's overall rate for thin pairs

Scored by top-1 accuracy and log-loss on a season the rates were not fitted on.
Pair counts are thin — most duos share few hundred possessions — so shrinkage is
the whole game here, exactly as the north-star notes warn about thin splits.

Output: data/parquet/assist_network.parquet
Usage: python scripts/132_assist_network.py
"""

from __future__ import annotations

import importlib.util
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PBP = ROOT / "data" / "parquet" / "pbp"
STINTS = ROOT / "data" / "parquet" / "stints"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
OUT = ROOT / "data" / "parquet" / "assist_network.parquet"

TRAIN = ["2022-23", "2023-24", "2024-25"]
TEST = "2025-26"
AST_RE = re.compile(r"\(([^()]+?)\s+\d+\s+AST\)")
# Raw pair counts are the wrong parameterisation: a duo with no shared history
# gets a near-zero weight, which is catastrophic in log-loss and common in
# practice (trades, rookies, new rotations). Model AFFINITY instead — observed
# assists over what volume alone predicts — and shrink that toward 1.0, so an
# unseen pair falls back to the baseline rather than to zero.
K_PAIR = 25.0


def load_120():
    spec = importlib.util.spec_from_file_location(
        "s120", ROOT / "scripts" / "120_build_stints.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def assists_for(season: str, S120):
    """-> list of (GAME_ID, elapsed_sec, team, scorer, passer) for assisted makes."""
    p = pd.read_parquet(PBP / f"{season}.parquet",
                        columns=["GAME_ID", "PERIOD", "ACTION_NUMBER", "SEC_REMAINING",
                                 "TEAM_ID", "PLAYER_ID", "ACTION_TYPE", "DESCRIPTION"])
    p = p[(p.ACTION_TYPE == "Made Shot") & p.DESCRIPTION.str.contains("AST", na=False)]
    pg = pd.read_parquet(PG, columns=["GAME_ID", "SEASON", "TEAM_ID", "PLAYER_ID",
                                      "firstName", "familyName"])
    pg = pg[pg.SEASON == season]
    roster = defaultdict(list)
    for r in pg.itertuples():
        roster[(r.GAME_ID, r.TEAM_ID)].append(
            {"PLAYER_ID": r.PLAYER_ID, "firstName": r.firstName, "familyName": r.familyName})
    idx = {k: S120.RosterIndex(v) for k, v in roster.items()}

    out, unresolved = [], 0
    for r in p.itertuples():
        if pd.isna(r.TEAM_ID) or pd.isna(r.PLAYER_ID):
            continue
        m = AST_RE.search(str(r.DESCRIPTION))
        if not m:
            continue
        ri = idx.get((r.GAME_ID, r.TEAM_ID))
        if ri is None:
            continue
        pid = ri.resolve(m.group(1), set(), set())
        if pid is None or pid == int(r.PLAYER_ID):
            unresolved += 1
            continue
        t = S120.elapsed(r.PERIOD, r.SEC_REMAINING if not pd.isna(r.SEC_REMAINING) else 0.0)
        out.append((r.GAME_ID, t, r.TEAM_ID, int(r.PLAYER_ID), int(pid)))
    return out, unresolved


def lineup_lookup(season: str):
    """-> {GAME_ID: [(start, end, team, frozenset lineup), ...]}"""
    f = STINTS / f"{season}.parquet"
    if not f.exists():
        return {}
    df = pd.read_parquet(f)
    out = defaultdict(list)
    for r in df.itertuples():
        out[r.GAME_ID].append((r.START_SEC, r.END_SEC, r.HOME_TEAM_ID, tuple(r.HOME_LINEUP)))
        out[r.GAME_ID].append((r.START_SEC, r.END_SEC, r.AWAY_TEAM_ID, tuple(r.AWAY_LINEUP)))
    return out


def on_court(lu, gid, t, team):
    for s, e, tid, five in lu.get(gid, ()):
        if tid == team and s <= t < e:
            return five
    return None


def within_season(S120, season="2024-25", split=0.6):
    """The FAIR test for pair chemistry: train on a season's early games and test
    on its later ones, so rosters are mostly intact. The cross-season test was
    structurally unfair — trades and signings had dissolved most pairs before
    they were ever scored."""
    rows, _ = assists_for(season, S120)
    lu = lineup_lookup(season)
    gids = sorted({r[0] for r in rows})
    cut = gids[int(len(gids) * split)]
    early = [r for r in rows if r[0] < cut]
    late = [r for r in rows if r[0] >= cut]
    pair, by_p, by_s, grand = defaultdict(float), defaultdict(float), defaultdict(float), 0.0
    for _, _, _, sc, pa in early:
        pair[(pa, sc)] += 1
        by_p[pa] += 1
        by_s[sc] += 1
        grand += 1
    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "AST_36"])
    a36 = {r.PLAYER_ID: r.AST_36 for r in ps[ps.SEASON == season].itertuples()}
    print(f"\nWITHIN-SEASON test, {season}: fit on {len(early):,} assists "
          f"from the first {int(split*100)}% of games, scored on {len(late):,}")
    print(f"  {'K_PAIR':>9}{'top-1 acc':>11}{'log-loss':>11}")
    base_h, base_ll, n = 0, [], 0
    for K in (0, 15, 40, 100, 300, 1000):
        h, lls = 0, []
        for gid, t, team, sc, pa in late:
            five = on_court(lu, gid, t, team)
            if not five or pa not in five or sc not in five:
                continue
            mates = [q for q in five if q != sc]
            if len(mates) < 2:
                continue
            b = np.array([a36.get(q, 1.0) + 1e-6 for q in mates]); b = b / b.sum()
            if K == 0:
                w = b
            else:
                aff = []
                for q in mates:
                    e = by_p.get(q, 0.0) * by_s.get(sc, 0.0) / max(grand, 1e-9)
                    aff.append((pair.get((q, sc), 0.0) + K) / (e + K))
                w = b * np.array(aff); w = w / w.sum()
            j = mates.index(pa)
            h += int(np.argmax(w) == j); lls.append(-np.log(max(w[j], 1e-9)))
        lab = "baseline (AST_36 only)" if K == 0 else str(K)
        print(f"  {lab:>9}{h/len(lls)*100:>10.1f}%{np.mean(lls):>11.4f}")
        if K == 0:
            base_h, base_ll, n = h, np.mean(lls), len(lls)
    print(f"  scored on {n:,} assisted makes")


def main() -> None:
    S120 = load_120()
    import sys
    if "--within" in sys.argv:
        within_season(S120)
        return

    # ---- fit pair rates on the training seasons ----
    pair = defaultdict(float)      # (passer, scorer) -> assists
    by_passer = defaultdict(float)
    by_scorer = defaultdict(float)
    grand = 0.0
    total_unres = 0
    for s in TRAIN:
        rows, unres = assists_for(s, S120)
        total_unres += unres
        for _, _, _, scorer, passer in rows:
            pair[(passer, scorer)] += 1
            by_passer[passer] += 1
            by_scorer[scorer] += 1
            grand += 1
        print(f"  {s}: {len(rows):,} assisted makes resolved", flush=True)
    print(f"  unresolved passer names: {total_unres:,}")

    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "AST_36"])
    ast36 = {}
    for r in ps[ps.SEASON.isin(TRAIN)].itertuples():
        ast36.setdefault(r.PLAYER_ID, []).append(r.AST_36)
    ast36 = {k: float(np.nanmean(v)) for k, v in ast36.items()}

    pd.DataFrame([{"passer": a, "scorer": b, "assists": n}
                  for (a, b), n in pair.items()]).to_parquet(OUT, index=False)
    print(f"\nassist_network: {len(pair):,} passer-scorer pairs -> {OUT}")

    # ---- test on a season the rates were not fitted on ----
    rows, _ = assists_for(TEST, S120)
    lu = lineup_lookup(TEST)
    print(f"\nTest season {TEST}: {len(rows):,} assisted makes")

    hit_b = hit_n = n = 0
    ll_b, ll_n = [], []
    for gid, t, team, scorer, passer in rows:
        five = on_court(lu, gid, t, team)
        if not five or passer not in five or scorer not in five:
            continue
        mates = [p for p in five if p != scorer]
        if len(mates) < 2:
            continue
        base = np.array([ast36.get(p, 1.0) + 1e-6 for p in mates])
        base = base / base.sum()
        # affinity = observed pair assists / volume-implied expectation, shrunk
        # toward 1. Multiplies the baseline rather than replacing it.
        aff = []
        for p in mates:
            exp_ps = by_passer.get(p, 0.0) * by_scorer.get(scorer, 0.0) / max(grand, 1e-9)
            aff.append((pair.get((p, scorer), 0.0) + K_PAIR) / (exp_ps + K_PAIR))
        net = base * np.array(aff)
        net = net / net.sum()
        j = mates.index(passer)
        hit_b += int(np.argmax(base) == j)
        hit_n += int(np.argmax(net) == j)
        ll_b.append(-np.log(max(base[j], 1e-9)))
        ll_n.append(-np.log(max(net[j], 1e-9)))
        n += 1

    print(f"  scored on {n:,} assisted makes with a reconstructed lineup\n")
    # sweep the shrinkage: if NO setting beats the baseline, the pair network is
    # genuinely not carrying portable signal, not merely mistuned
    print(f"  {'K_PAIR':>9}{'top-1 acc':>11}{'log-loss':>11}")
    for Kt in (5, 12, 25, 50, 100, 250, 600):
        h = 0; lls = []
        for gid, t, team, scorer, passer in rows:
            five = on_court(lu, gid, t, team)
            if not five or passer not in five or scorer not in five:
                continue
            mates = [q for q in five if q != scorer]
            if len(mates) < 2:
                continue
            b = np.array([ast36.get(q, 1.0) + 1e-6 for q in mates]); b = b / b.sum()
            a = []
            for q in mates:
                e = by_passer.get(q, 0.0) * by_scorer.get(scorer, 0.0) / max(grand, 1e-9)
                a.append((pair.get((q, scorer), 0.0) + Kt) / (e + Kt))
            w = b * np.array(a); w = w / w.sum()
            jj = mates.index(passer)
            h += int(np.argmax(w) == jj); lls.append(-np.log(max(w[jj], 1e-9)))
        print(f"  {Kt:>9}{h/len(lls)*100:>10.1f}%{np.mean(lls):>11.4f}")
    print()
    print(f"  {'model':<34}{'top-1 acc':>11}{'log-loss':>11}")
    print(f"  {'baseline: AST_36 weighting':<34}{hit_b/n*100:>10.1f}%{np.mean(ll_b):>11.4f}")
    print(f"  {'network: pair-specific rates':<34}{hit_n/n*100:>10.1f}%{np.mean(ll_n):>11.4f}")
    print(f"  {'random among 4 teammates':<34}{25.0:>10.1f}%{np.log(4):>11.4f}")
    gain = (np.mean(ll_n) / np.mean(ll_b) - 1) * 100
    print(f"\n  pair network vs baseline: {gain:+.1f}% log-loss "
          f"({'better' if gain < 0 else 'worse'})")


if __name__ == "__main__":
    main()
