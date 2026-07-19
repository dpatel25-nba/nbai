"""
Do our models beat the market? — ATS, totals, and moneyline edge vs closing lines.

The closing line is the sharpest benchmark there is. This is the real test of
"predictive gains": not MAE vs reality, but whether we'd win money against the
price. Beating ~52.4% ATS/totals (the -110 breakeven) or a positive ML ROI would
be a genuine edge; matching the market (≈50%, negative ROI after vig) is the
honest expected result for a public-data model — and tells us where NOT to look.

Uses walk-forward sim_mode1 predictions (leakage-free) vs. game_lines closing
numbers. Reports overall, then stratified by how far we disagree with the line.

Usage: python scripts/103_market_edge.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LINES = ROOT / "data" / "parquet" / "game_lines.parquet"
SIM = ROOT / "data" / "features" / "sim_mode1_predictions.parquet"
ELO = ROOT / "data" / "features" / "elo_predictions.parquet"

BREAKEVEN = 0.5238   # -110 vig


def implied(ml):
    ml = np.asarray(ml, float)
    ml = np.where(ml == 0, np.nan, ml)
    return np.where(ml > 0, 100 / (ml + 100), -ml / (-ml + 100))


def ml_profit(ml, won):
    ml = np.asarray(ml, float)
    payout = np.where(ml > 0, ml / 100, 100 / -ml)
    return np.where(won, payout, -1.0)


def wr(mask_pick_correct, mask_valid):
    n = mask_valid.sum()
    return mask_pick_correct[mask_valid].mean(), n


def main() -> None:
    lines = pd.read_parquet(LINES)
    sim = pd.read_parquet(SIM)
    elo = pd.read_parquet(ELO)[["GAME_ID", "P_HOME"]].rename(columns={"P_HOME": "P_ELO"})
    d = lines.merge(sim, on="GAME_ID", how="inner").merge(elo, on="GAME_ID", how="left")
    d = d[(d.SEASON_TYPE_x == "Regular Season") & d.pred_margin.notna()].copy()
    print(f"Market-edge backtest — {len(d):,} games with lines + predictions "
          f"({d.SEASON_x.min()}…{d.SEASON_x.max()})\n")

    # ---- Against the spread ----
    # home covers if act_margin + home_spread > 0; we pick home if pred beats the line
    cover_home = d.act_margin + d.home_spread > 0
    push = d.act_margin + d.home_spread == 0
    pick_home = d.pred_margin + d.home_spread > 0
    correct = (pick_home & cover_home) | (~pick_home & ~cover_home)
    valid = ~push
    r, n = wr(correct.to_numpy(), valid.to_numpy())
    print(f"ATS   (vs closing spread)   {r*100:5.2f}%  over {n:,} games   "
          f"[breakeven {BREAKEVEN*100:.1f}%]  {'EDGE' if r > BREAKEVEN else 'no edge'}")

    # ---- Totals ----
    over = d.act_total > d.total
    pushT = d.act_total == d.total
    pick_over = d.pred_total > d.total
    correctT = (pick_over & over) | (~pick_over & ~over)
    r2, n2 = wr(correctT.to_numpy(), (~pushT).to_numpy())
    print(f"Totals (vs closing total)   {r2*100:5.2f}%  over {n2:,} games   "
          f"{'EDGE' if r2 > BREAKEVEN else 'no edge'}")

    # ---- Moneyline: our win prob vs the (de-vigged) market prob ----
    imp_h, imp_a = implied(d.ml_home), implied(d.ml_away)
    mkt_home = imp_h / (imp_h + imp_a)              # de-vigged market prob
    for name, P in [("sim P_HOME", d.P_HOME.to_numpy()), ("Elo P_HOME", d.P_ELO.to_numpy())]:
        if np.isnan(P).all():
            continue
        brier_us = np.nanmean((P - d.HOME_WIN.to_numpy()) ** 2)
        brier_mkt = np.nanmean((mkt_home - d.HOME_WIN.to_numpy()) ** 2)
        # bet the side where our prob exceeds the de-vigged market prob (model sees value)
        bet_home = P > mkt_home
        won = np.where(bet_home, d.HOME_WIN.to_numpy() == 1, d.HOME_WIN.to_numpy() == 0)
        ml_used = np.where(bet_home, d.ml_home.to_numpy(), d.ml_away.to_numpy())
        valid = ~np.isnan(P) & ~np.isnan(ml_used)
        roi = ml_profit(ml_used[valid], won[valid]).mean()
        print(f"\n{name}:  Brier {brier_us:.4f}  vs market {brier_mkt:.4f}  "
              f"({'better' if brier_us < brier_mkt else 'worse'})")
        print(f"   ML ROI betting our value side: {roi*100:+.2f}%  over {valid.sum():,} bets "
              f"({'PROFIT' if roi > 0 else 'loss'})")

    # ---- ATS stratified by how far we disagree with the line (edge magnitude) ----
    print("\nATS by disagreement with the spread (|pred_margin - market_spread|):")
    disagree = (d.pred_margin + d.home_spread).abs()
    q = disagree.quantile([0.5, 0.8, 0.95])
    for lbl, lo in [("all", 0), ("top 50% conf", q.iloc[0]),
                    ("top 20% conf", q.iloc[1]), ("top 5% conf", q.iloc[2])]:
        mask = (disagree >= lo).to_numpy() & valid_ats(push)
        if mask.sum():
            rr = correct.to_numpy()[mask].mean()
            print(f"  {lbl:<16} {rr*100:5.2f}%  ({mask.sum():,} games)")


def valid_ats(push):
    return (~push).to_numpy()


if __name__ == "__main__":
    main()
