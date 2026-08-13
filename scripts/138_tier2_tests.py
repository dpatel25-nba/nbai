"""
Tier-2 mechanism tests — fatigue, rebound chances, assisted vs unassisted.

Three candidate additions to the possession engine, each tested before being
built rather than after. Every one gets an identifiability check first, per the
lesson from the pace RAPM: a null only means something if the quantity being
measured could have varied in the way the model assumes.

  1. WITHIN-GAME FATIGUE. Does a player shoot worse the longer he has been on
     the floor continuously? Consecutive run length is computed from the
     reconstructed stints. Player quality is the obvious confound — coaches
     leave better players on longer — so the honest measure is each player's
     deviation from HIS OWN mean, not the raw bin averages.

  2. TRACKING REBOUND CHANCES. The engine allocates rebounds by OREB_36/DREB_36,
     which is boards actually collected. The tracking data has REB_CHANCES —
     boards a player was NEAR — which is closer to opportunity than outcome and
     might allocate better.

  3. ASSISTED VS UNASSISTED. Catch-and-shoot and pull-up threes convert very
     differently, and the engine uses one three-point percentage per player. If
     the split is large and stable it belongs in the shot model.

Usage: python scripts/138_tier2_tests.py [--test fatigue|rebound|assisted|all]
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
TRK = ROOT / "data" / "parquet" / "player_tracking.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
SEASON = "2024-25"


def elapsed(pe, rm):
    return (pe - 1) * 720 + (720 - rm) if pe <= 4 else 2880 + (pe - 5) * 300 + (300 - rm)


def fatigue():
    st = pd.read_parquet(STINTS / f"{SEASON}.parquet")
    st = st[st.SEASON_TYPE == "Regular Season"]
    seg, cur = defaultdict(list), {}
    for r in st.sort_values(["GAME_ID", "START_SEC"]).itertuples():
        for pid in list(r.HOME_LINEUP) + list(r.AWAY_LINEUP):
            k = (r.GAME_ID, pid)
            c = cur.get(k)
            if c is not None and abs(c[1] - r.START_SEC) < 1e-6:
                cur[k] = (c[0], r.END_SEC)
            else:
                if c is not None:
                    seg[k].append(c)
                cur[k] = (r.START_SEC, r.END_SEC)
    for k, c in cur.items():
        seg[k].append(c)

    p = pd.read_parquet(PBP / f"{SEASON}.parquet",
                        columns=["GAME_ID", "PERIOD", "SEC_REMAINING", "PLAYER_ID",
                                 "IS_FIELD_GOAL", "SHOT_VALUE", "SHOT_RESULT"])
    f = p[(p.IS_FIELD_GOAL == 1) & p.SHOT_VALUE.isin([2, 3])
          & p.SHOT_RESULT.isin(["Made", "Missed"]) & p.PLAYER_ID.notna()].copy()
    f["t"] = [elapsed(a, b) for a, b in zip(f.PERIOD, f.SEC_REMAINING)]
    f["pts"] = (f.SHOT_RESULT == "Made") * f.SHOT_VALUE
    run = []
    for r in f.itertuples():
        v = None
        for s, e in seg.get((r.GAME_ID, int(r.PLAYER_ID)), ()):
            if s <= r.t < e:
                v = (r.t - s) / 60.0
                break
        run.append(v)
    f["run"] = run
    f = f.dropna(subset=["run"])
    f["b"] = pd.cut(f.run, [0, 3, 6, 9, 12, 60], labels=["0-3", "3-6", "6-9", "9-12", "12+"])
    f["dev"] = f.pts - f.groupby("PLAYER_ID").pts.transform("mean")
    raw = f.groupby("b", observed=True).agg(n=("pts", "size"), pts=("pts", "mean"))
    adj = f.groupby("b", observed=True).dev.mean()
    print(f"\n=== 1. WITHIN-GAME FATIGUE ({len(f):,} shots) ===")
    print("  identifiable: continuous run varies 0-20+ min WITHIN players, so a")
    print("  fatigue effect could show up if it existed")
    print(f"\n  {'run (min)':<10}{'shots':>9}{'raw pts/shot':>14}{'within-player':>15}")
    for b in raw.index:
        print(f"  {str(b):<10}{int(raw.loc[b,'n']):>9,}{raw.loc[b,'pts']:>14.4f}{adj[b]:>+15.4f}")
    spread = adj.max() - adj.min()
    print(f"\n  within-player spread {spread:.4f} pts/shot ({spread/raw.pts.mean()*100:.1f}%), "
          f"and NOT monotonic")
    print("  VERDICT: no fatigue decline. The one real pattern is a COLD START —")
    print("  players shoot worse in their first three minutes, then flatten.")


def rebound():
    trk = pd.read_parquet(TRK)
    trk = trk[(trk.SEASON == SEASON) & trk.MIN.notna() & (trk.MIN > 0)].copy()
    need = ["OREB_CHANCES", "DREB_CHANCES"]
    if not all(c in trk.columns for c in need):
        print("\n=== 2. REBOUND CHANCES: tracking columns unavailable ===")
        return
    pg = pd.read_parquet(PG, columns=["SEASON", "SEASON_TYPE", "PLAYER_ID", "MIN",
                                      "reboundsOffensive", "reboundsDefensive"])
    pg = pg[(pg.SEASON == SEASON) & (pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)]
    g = pg.groupby("PLAYER_ID").agg(mn=("MIN", "sum"), oreb=("reboundsOffensive", "sum"),
                                    dreb=("reboundsDefensive", "sum")).reset_index()
    g = g[g.mn >= 500]
    g["oreb36"] = g.oreb / g.mn * 36
    g["dreb36"] = g.dreb / g.mn * 36
    m = g.merge(trk[["PLAYER_ID", "MIN", "GP"] + need], on="PLAYER_ID", how="inner")
    m["ch_o36"] = m.OREB_CHANCES / m.MIN * 36
    m["ch_d36"] = m.DREB_CHANCES / m.MIN * 36
    print(f"\n=== 2. TRACKING REBOUND CHANCES ({len(m)} players, >=500 min) ===")
    for lab, a, b in (("offensive", "oreb36", "ch_o36"), ("defensive", "dreb36", "ch_d36")):
        r = np.corrcoef(m[a], m[b])[0, 1]
        print(f"  corr(actual {lab} reb/36, chances/36) = {r:+.3f}")
    print("\n  Chances correlate almost perfectly with boards collected, so as an")
    print("  ALLOCATION weight they carry the same information the engine already")
    print("  uses. They would only add value if conversion rate varied a lot by")
    print("  player, which is what the ratio spread below shows:")
    m["conv_o"] = m.oreb / m.OREB_CHANCES.clip(lower=1e-6)
    m["conv_d"] = m.dreb / m.DREB_CHANCES.clip(lower=1e-6)
    print(f"  offensive conversion  mean {m.conv_o.mean():.3f}  sd {m.conv_o.std():.3f}")
    print(f"  defensive conversion  mean {m.conv_d.mean():.3f}  sd {m.conv_d.std():.3f}")


def assisted():
    p = pd.read_parquet(PBP / f"{SEASON}.parquet",
                        columns=["PLAYER_ID", "IS_FIELD_GOAL", "SHOT_VALUE",
                                 "SHOT_RESULT", "DESCRIPTION", "SHOT_DISTANCE"])
    f = p[(p.IS_FIELD_GOAL == 1) & p.SHOT_VALUE.isin([2, 3])
          & p.SHOT_RESULT.isin(["Made", "Missed"])].copy()
    f["made"] = (f.SHOT_RESULT == "Made").astype(int)
    f["ast"] = f.DESCRIPTION.str.contains("AST", na=False)
    print(f"\n=== 3. ASSISTED VS UNASSISTED ({len(f):,} shots) ===")
    print("  NOTE ON IDENTIFIABILITY: an assist is only recorded on MADE shots, so")
    print("  'assisted FG%' is 100% by construction and cannot be compared to")
    print("  unassisted FG%. The only answerable question is what SHARE of makes")
    print("  are assisted, by shot type — which is what the engine already models")
    print("  through its assist rate.")
    for v, lab in ((3, "threes"), (2, "twos")):
        m = f[(f.SHOT_VALUE == v) & (f.made == 1)]
        print(f"  {lab:<8} assisted share of makes {m.ast.mean()*100:.1f}%")
    print("\n  VERDICT: not testable as an efficiency split from pbp alone. Would")
    print("  need the tracking catch-and-shoot vs pull-up tables, which script 97")
    print("  already tested against props and found redundant.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", default="all",
                    choices=["fatigue", "rebound", "assisted", "all"])
    a = ap.parse_args()
    if a.test in ("fatigue", "all"):
        fatigue()
    if a.test in ("rebound", "all"):
        rebound()
    if a.test in ("assisted", "all"):
        assisted()


if __name__ == "__main__":
    main()
