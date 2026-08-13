"""
In-season rate updating — the simulator's largest blind spot.

The possession simulator projects every player from PRIOR SEASONS ONLY (Marcel,
script 124). By January that means simulating a game while ignoring forty games
of evidence about the player taking the shot. Measured directly on 2025-26, an
empirical-Bayes update using in-season data cuts scoring-rate error by 5.0% — and
that gap is most of why the simulator trails the props engine on player means
(5.05 vs 4.59 MAE), since the props engine has always used recent form.

The update is the standard conjugate form, applied per statistic:

    theta_hat = (m_td * x_todate + K * marcel_prior) / (m_td + K)

Two things are worth stating because they are not obvious:

 1. THE IN-SEASON K IS MUCH SMALLER THAN THE ACROSS-SEASON K. Script 126 found
    K≈1000 for projecting PTS_36 across seasons; the in-season optimum is ~200.
    That is not a contradiction — across seasons you must discount for aging,
    role change and team change, whereas within a season you are watching the
    same player in the same situation, so each minute of evidence counts for
    roughly five times as much.

 2. DIFFERENT QUANTITIES WANT DIFFERENT MEMORY. Script 130 found minutes are best
    tracked by fast exponential smoothing, because role drifts. Scoring RATE is
    the opposite: season-to-date (MAE 6.038) beats EWMA (6.143), because
    efficiency is stable and a longer average is simply less noisy. So volume and
    efficiency use a cumulative in-season average, while the rotation model keeps
    its EWMA. Using one memory for both would damage whichever it fits worse.

Every accumulator is strictly prior-to-tipoff, so the output stays leakage-safe.

Output: data/parquet/ingame_rates/<season>.parquet — one row per (GAME_ID,
PLAYER_ID) with the blended rate profile the simulator consumes.

Usage:
  python scripts/131_ingame_rates.py --season 2025-26
"""

from __future__ import annotations

import argparse
import importlib.util
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "data" / "parquet" / "player_games.parquet"
OUT_DIR = ROOT / "data" / "parquet" / "ingame_rates"

# in-season shrinkage, in minutes of evidence. Volume settles fast; percentages
# are far noisier per minute and need much more before they are trusted.
K_RATE = 200.0
K_PCT = 900.0

RATE_COLS = ["FG2A_36", "FG3A_36", "FTA_36", "TOV_36", "OREB_36", "DREB_36",
             "AST_36", "PF_36"]
PCT_COLS = ["FG2_PCT", "FG3_PCT", "FT_PCT"]

SRC = {
    "FG2A_36": ("fg2a", "MIN"), "FG3A_36": ("threePointersAttempted", "MIN"),
    "FTA_36": ("freeThrowsAttempted", "MIN"), "TOV_36": ("turnovers", "MIN"),
    "OREB_36": ("reboundsOffensive", "MIN"), "DREB_36": ("reboundsDefensive", "MIN"),
    "AST_36": ("assists", "MIN"), "PF_36": ("foulsPersonal", "MIN"),
}
PCT_SRC = {
    "FG2_PCT": ("fg2m", "fg2a"),
    "FG3_PCT": ("threePointersMade", "threePointersAttempted"),
    "FT_PCT": ("freeThrowsMade", "freeThrowsAttempted"),
}


def load_sim():
    spec = importlib.util.spec_from_file_location(
        "sim124", ROOT / "scripts" / "124_possession_sim.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run_season(season: str, S) -> pd.DataFrame:
    prior, _ = S.build_rates(season)             # prior-season Marcel profiles
    cols = ["GAME_ID", "GAME_DATE", "SEASON", "SEASON_TYPE", "PLAYER_ID", "MIN",
            "fieldGoalsAttempted", "fieldGoalsMade", "threePointersAttempted",
            "threePointersMade", "freeThrowsAttempted", "freeThrowsMade",
            "turnovers", "reboundsOffensive", "reboundsDefensive", "assists",
            "foulsPersonal"]
    pg = pd.read_parquet(PG, columns=cols)
    pg = pg[(pg.SEASON == season) & (pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)]
    pg = pg.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)
    pg["fg2a"] = pg.fieldGoalsAttempted - pg.threePointersAttempted
    pg["fg2m"] = pg.fieldGoalsMade - pg.threePointersMade

    acc = defaultdict(lambda: defaultdict(float))
    rows = []
    for r in pg.itertuples():
        pid = int(r.PLAYER_ID)
        a = acc[pid]
        base = prior[pid]                        # RateBook falls back if unseen
        rec = {"GAME_ID": r.GAME_ID, "PLAYER_ID": pid, "SEASON": season}
        m_td = a["MIN"]
        for c in RATE_COLS:
            src, _ = SRC[c]
            obs = (a[src] / m_td * 36.0) if m_td > 0 else base[c]
            rec[c] = (m_td * obs + K_RATE * base[c]) / (m_td + K_RATE)
        for c in PCT_COLS:
            mk, at = PCT_SRC[c]
            # percentages are weighted by MINUTES so K_PCT is on one scale, but
            # the observed value uses attempts; with no attempts yet, fall back
            obs = (a[mk] / a[at]) if a[at] > 0 else base[c]
            rec[c] = (m_td * obs + K_PCT * base[c]) / (m_td + K_PCT)
        rec["FGA_36"] = rec["FG2A_36"] + rec["FG3A_36"]
        rec["MPG"] = base["MPG"]
        rec["m_todate"] = m_td
        rows.append(rec)

        a["MIN"] += r.MIN
        for src in ("fg2a", "fg2m", "threePointersAttempted", "threePointersMade",
                    "freeThrowsAttempted", "freeThrowsMade", "turnovers",
                    "reboundsOffensive", "reboundsDefensive", "assists",
                    "foulsPersonal"):
            a[src] += getattr(r, src)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025-26")
    args = ap.parse_args()
    S = load_sim()
    df = run_season(args.season, S)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_DIR / f"{args.season}.parquet", index=False)
    print(f"ingame_rates/{args.season}: {len(df):,} player-games")

    # sanity: the blend must start at the prior and converge to observed form
    early = df[df.m_todate < 100]
    late = df[df.m_todate > 1200]
    print(f"  early-season rows (<100 min banked): {len(early):,}")
    print(f"  late-season rows  (>1200 min banked): {len(late):,}")
    print(f"  mean FG3_PCT  early {early.FG3_PCT.mean():.3f}  late {late.FG3_PCT.mean():.3f}")
    print(f"  spread of FG3A_36  early sd {early.FG3A_36.std():.2f}  "
          f"late sd {late.FG3A_36.std():.2f}   (should widen as evidence accrues)")
    print(f"\nSaved -> {OUT_DIR / (args.season + '.parquet')}")


if __name__ == "__main__":
    main()
