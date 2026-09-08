"""
League drift — the rate book lags the league it is projecting into.

Every per-36 rate the engine consumes is a projection built from PRIOR seasons
and then updated in-season (script 131). Measured against what the league
actually did in 2024-25, that machinery still leaves systematic bias:

    rate        prior book   in-season   verdict
    FG2A_36          1.059       1.001    fine after updating
    FG3A_36          0.915       0.958    4.2% UNDER
    FTA_36           1.063       1.031    3.1% over
    OREB_36          0.914       0.948    5.2% UNDER
    AST_36           0.976       0.977    2.3% under
    PF_36            1.045       1.035    3.5% over

These are the well-known league trends — three-point volume and offensive
rebounding rising, fouls and free throws falling — and a projection that
regresses toward a multi-year mean necessarily lags them. Free throws are the
worst case because league volume is genuinely volatile year to year (47.1, 43.4,
43.3, 47.0 across consecutive seasons, driven by officiating), so regression
toward a multi-year mean lands nowhere near the target season.

THE CORRECTION. For each rate, scale every player's projection by

    league level observed SO FAR THIS SEASON / league level the book implies

which pins the aggregate to the season being simulated while leaving the spread
ACROSS players untouched — the part the book is actually good at. Nothing about
who shoots more than whom changes; only the league-wide level moves.

LEAKAGE. The factor for a game uses only games played STRICTLY BEFORE its date,
accumulated forward through the season. A factor computed from the full season
would be reading the answer, and the early-season factors are deliberately
shrunk toward 1.0 because a handful of games estimates a league level poorly.

Output: data/parquet/league_drift.parquet   (GAME_ID x rate factor)
Usage: python scripts/153_league_drift.py [--seasons 2023-24,2024-25]
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "data" / "parquet" / "player_games.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT = ROOT / "data" / "parquet" / "league_drift.parquet"

# rate column -> the box-score field it aggregates
FIELDS = {"FG3A_36": "threePointersAttempted", "FTA_36": "freeThrowsAttempted",
          "TOV_36": "turnovers", "OREB_36": "reboundsOffensive",
          "DREB_36": "reboundsDefensive", "AST_36": "assists",
          "PF_36": "foulsPersonal", "FG2A_36": None}
K_SHRINK = 25000.0     # minutes of prior belief; ~50 games before the factor bites


def load_sim():
    spec = importlib.util.spec_from_file_location(
        "sim", ROOT / "scripts" / "124_possession_sim.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2023-24,2024-25,2025-26")
    args = ap.parse_args()
    S = load_sim()

    cols = ["GAME_ID", "GAME_DATE", "SEASON", "SEASON_TYPE", "PLAYER_ID", "MIN",
            "fieldGoalsAttempted"] + [v for v in FIELDS.values() if v]
    pg = pd.read_parquet(PG, columns=list(dict.fromkeys(cols)))
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)]

    out = []
    for season in [s.strip() for s in args.seasons.split(",") if s.strip()]:
        d = pg[pg.SEASON == season].copy()
        if len(d) < 5000:
            print(f"  {season}: too few rows, skipped")
            continue
        d["FG2A"] = d.fieldGoalsAttempted - d.threePointersAttempted

        # ---- what the rates ACTUALLY IN USE imply, game by game ----
        # An earlier version measured the gap against the prior-season book.
        # But the pipeline applies in-season updates (script 131) BEFORE this
        # correction, and those already remove most of the drift — so a
        # book-derived factor double-corrects. It made FG2A 1.005 -> 0.960 and
        # TOV 1.013 -> 1.046, breaking two columns that had been fine. The
        # factor must be the RESIDUAL bias of the rates the engine really uses.
        rates, _ = S.build_rates(season)
        igf = ROOT / "data" / "parquet" / "ingame_rates" / f"{season}.parquet"
        ig = pd.read_parquet(igf) if igf.exists() else None
        ig_map = {}
        if ig is not None:
            have = [c for c in FIELDS if c in ig.columns]
            for r in ig.itertuples():
                ig_map[(r.GAME_ID, int(r.PLAYER_ID))] = {
                    c: float(getattr(r, c)) for c in have}

        impl = {c: np.zeros(len(d)) for c in FIELDS}
        pid_a = d.PLAYER_ID.to_numpy()
        gid_a = d.GAME_ID.to_numpy()
        mn_a = d.MIN.to_numpy(dtype=float) / 36.0
        for i in range(len(d)):
            pid = int(pid_a[i])
            base_r = rates[pid]
            over = ig_map.get((gid_a[i], pid), {})
            for c in FIELDS:
                v = over.get(c, base_r.get(c))
                if v is not None and np.isfinite(v):
                    impl[c][i] = float(v) * mn_a[i]
        for c in FIELDS:
            d["imp_" + c] = impl[c]

        agg = {"mn": ("MIN", "sum")}
        for c in FIELDS:
            agg["act_" + c] = (FIELDS[c] or "FG2A", "sum")
            agg["imp_" + c] = ("imp_" + c, "sum")
        gday = d.groupby(["GAME_DATE", "GAME_ID"]).agg(**agg).reset_index()
        day = gday.groupby("GAME_DATE").sum(numeric_only=True).sort_index()
        cum = day.cumsum().shift(1).fillna(0.0)       # STRICTLY prior days

        rows = []
        for date, r in cum.iterrows():
            f = {}
            for c in FIELDS:
                imp = float(r["imp_" + c])
                act = float(r["act_" + c])
                mn = float(r["mn"])
                if imp <= 0 or mn <= 0:
                    f[c] = 1.0
                    continue
                raw = act / imp
                w = mn / (mn + K_SHRINK)
                f[c] = 1.0 + w * (raw - 1.0)
            rows.append({"GAME_DATE": date, **f})
        fac = pd.DataFrame(rows)

        g = d[["GAME_ID", "GAME_DATE"]].drop_duplicates()
        g = g.merge(fac, on="GAME_DATE", how="left")
        g["SEASON"] = season
        out.append(g)

        last = fac.iloc[-1]
        print(f"  {season}: {len(g):,} games   end-of-season factors  " +
              "  ".join(f"{c.replace('_36','')} {last[c]:.3f}" for c in
                        ("FG3A_36", "FTA_36", "OREB_36", "PF_36")))

    if not out:
        raise SystemExit("no seasons produced factors")
    res = pd.concat(out, ignore_index=True)
    res.to_parquet(OUT, index=False)
    print(f"\nSaved {len(res):,} game-factor rows -> {OUT}")
    print("  A factor of 1.03 means the league is doing 3% MORE of that per")
    print("  minute than the book projects, so player rates are scaled up 3%.")


if __name__ == "__main__":
    main()
