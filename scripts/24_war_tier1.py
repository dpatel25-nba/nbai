"""
Tier-1 WAR (box-based) — entrant #1 in the WAR bake-off.

Approach (transparent, unblocked, validated):
  1. Derive the point value of box stats by regressing each team-season's net
     rating on its per-100 box rates (OLS).  -> linear weights, BPM-style.
  2. Apply those weights to each player's per-100 box rates -> raw box impact/100,
     centered so a minutes-weighted league-average player = 0 (a "box plus-minus").
  3. Above replacement (-2.0/100), scaled by playing time -> VORP -> WAR (x2.7).
  4. Validate: summed team WAR vs. actual team wins, plus face validity (top players).

Box metrics are offense-tilted and weak on defense — that's the known Tier-1 limit
the RAPM entrant will beat. Output: adds WAR/BPM columns to player_seasons_war.parquet.

Usage: python scripts/24_war_tier1.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
TG = ROOT / "data" / "parquet" / "team_games.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT = ROOT / "data" / "parquet" / "player_seasons_war.parquet"

REPLACEMENT = -2.0     # BPM points/100 of a replacement player
PTS_PER_WIN = 30.0     # ~30 points ≈ 1 win
VORP_TO_WAR = 2.7      # Basketball-Reference convention

# box features (per-100 possessions) used as impact inputs
FEATS = ["PTS", "FG3M", "FTM", "AST", "OREB", "DREB", "STL", "BLK", "TOV", "PF"]
TOTALS = {"PTS": "points", "FG3M": "threePointersMade", "FTM": "freeThrowsMade",
          "AST": "assists", "OREB": "reboundsOffensive", "DREB": "reboundsDefensive",
          "STL": "steals", "BLK": "blocks", "TOV": "turnovers", "PF": "foulsPersonal"}


def team_training():
    """Team-season per-100 box rates (X), net rating (y), and net lookup by (tricode, season)."""
    tg = pd.read_parquet(TG)
    tg = tg[tg["SEASON_TYPE"] == "Regular Season"]
    rows, net_lookup = [], {}
    for (tri, season), d in tg.groupby(["TEAM_TRICODE", "SEASON"]):
        poss = (d["fieldGoalsAttempted"].sum() + 0.44 * d["freeThrowsAttempted"].sum()
                - d["reboundsOffensive"].sum() + d["turnovers"].sum())
        if poss <= 0:
            continue
        rec = {f: d[TOTALS[f]].sum() / poss * 100 for f in FEATS}
        rec["net"] = (d["offensiveRating"] - d["defensiveRating"]).mean()
        rows.append(rec)
        net_lookup[(tri, season)] = rec["net"]
    t = pd.DataFrame(rows)
    X = np.column_stack([np.ones(len(t))] + [t[f].to_numpy() for f in FEATS])
    return X, t["net"].to_numpy(), net_lookup


def main() -> None:
    X, y, net_lookup = team_training()
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ beta
    r2 = 1 - np.sum((y - pred) ** 2) / np.sum((y - y.mean()) ** 2)
    print(f"Team net-rating regression: {len(y)} team-seasons, R² = {r2:.3f}")

    # --- apply weights to players (raw box value per 100) ---
    ps = pd.read_parquet(PS).copy()
    ps["POSS"] = ps["MIN"] * ps["PACE"] / 48.0
    ps = ps[ps["POSS"] > 0].copy()
    per100 = {f: ps[TOTALS[f]] / ps["POSS"] * 100 for f in FEATS}
    ps["raw"] = (beta[0] + sum(beta[i + 1] * per100[f] for i, f in enumerate(FEATS))).to_numpy()

    # center per season: minutes-weighted average player = 0
    wm = ps.groupby("SEASON").apply(lambda g: np.average(g["raw"], weights=g["MIN"]),
                                    include_groups=False)
    bpm0 = ps["raw"] - ps["SEASON"].map(wm)
    # shrink toward average for small samples (per-100 is noisy at low minutes)
    bpm0 = bpm0 * ps["MIN"] / (ps["MIN"] + 600)
    # normalize to a realistic plus-minus spread (rotation-player SD ≈ 2.8)
    sd = bpm0[ps["MIN"] >= 1000].std()
    ps["BPM"] = (bpm0 * (2.8 / sd)).round(2)

    # split BPM into offensive & defensive components (they sum to BPM)
    OFF = {"PTS", "FG3M", "FTM", "AST", "OREB", "TOV"}
    o_raw = sum(beta[i + 1] * per100[f] for i, f in enumerate(FEATS) if f in OFF)
    d_raw = sum(beta[i + 1] * per100[f] for i, f in enumerate(FEATS) if f not in OFF)
    for lbl, rawv in (("OBPM", o_raw), ("DBPM", d_raw)):
        ps["_r"] = rawv.to_numpy()
        wmm = ps.groupby("SEASON").apply(lambda g: np.average(g["_r"], weights=g["MIN"]),
                                         include_groups=False)
        comp = (ps["_r"] - ps["SEASON"].map(wmm)) * ps["MIN"] / (ps["MIN"] + 600)
        ps[lbl] = (comp * (2.8 / sd)).round(2)
    ps = ps.drop(columns=["_r"])

    # VORP -> WAR, then anchor so league total WAR/season ≈ wins above replacement.
    # ~490 = 1230 team wins − 30 teams × ~0.30 replacement win% × 82.
    share = ps["MIN"] / (5 * 48 * ps["GP"])
    war_raw = (ps["BPM"] - REPLACEMENT) * share * (ps["GP"] / 82.0) * VORP_TO_WAR
    scale_w = 490.0 / war_raw.groupby(ps["SEASON"]).sum().mean()
    ps["WAR"] = (war_raw * scale_w).round(2)
    ps["VORP"] = (ps["WAR"] / VORP_TO_WAR).round(2)
    q = ps[ps["MIN"] >= 1000]
    print(f"BPM sd-normalized; WAR anchored to ~490 WAR/season (×{scale_w:.2f})")
    print(f"BPM range (≥1000 min): {q.BPM.min():.1f} … {q.BPM.max():.1f}  (sd {q.BPM.std():.2f})")
    ps.drop(columns=["raw", "POSS"]).to_parquet(OUT, index=False)

    # --- validation 1: team WAR vs actual wins ---
    games = pd.read_parquet(GAMES)
    rs = games[games["SEASON_TYPE"] == "Regular Season"]
    wins = {}
    for r in rs.itertuples():
        wins.setdefault((r.HOME_TEAM, r.SEASON), 0); wins.setdefault((r.AWAY_TEAM, r.SEASON), 0)
        if r.HOME_WIN: wins[(r.HOME_TEAM, r.SEASON)] += 1
        else: wins[(r.AWAY_TEAM, r.SEASON)] += 1
    tw = ps.groupby(["TEAM", "SEASON"])["WAR"].sum().reset_index()
    tw["actual_wins"] = tw.apply(lambda r: wins.get((r.TEAM, r.SEASON), np.nan), axis=1)
    tw = tw.dropna()
    corr = np.corrcoef(tw["WAR"], tw["actual_wins"])[0, 1]
    print(f"\nValidation — team totals: sum(player WAR) vs actual team wins")
    print(f"  correlation = {corr:.3f}  over {len(tw):,} team-seasons")
    print(f"  (a team's summed WAR + ~{0.27*82:.0f} replacement wins should ≈ its win total)")

    # --- validation 2: face validity ---
    print("\nTop 10 player-seasons by WAR (should be MVP-caliber):")
    top = ps.nlargest(10, "WAR")[["PLAYER", "TEAM", "SEASON", "MPG", "PTS_PG", "BPM", "WAR"]]
    print(top.to_string(index=False))


if __name__ == "__main__":
    main()
