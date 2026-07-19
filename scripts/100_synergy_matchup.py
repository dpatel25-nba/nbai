"""
Bottom-up play-type matchup projection — does it beat the shallow rating-diff?

The north-star thesis: a team's scoring vs an opponent depends on HOW its offense
(play-type mix) meets that opponent's play-type-specific defense — not just the two
aggregate ratings. Test it head-to-head, predicting each team's ACTUAL per-game
offensive rating, both methods sharing the same offense baseline (A's own off
rating), differing ONLY in the opponent adjustment:

  top-down  : A_base + (B_def_rating - lg_def_rating)                      [aggregate]
  bottom-up : A_base + 100·Σ_pt freqA_pt·(B_def_ppp_pt - lg_def_ppp_pt)    [play-type weighted]

If bottom-up wins, the play-type decomposition captures specific matchups the
aggregate misses (e.g. an iso-heavy offense vs an iso-stout defense). All profiles
are PRIOR-SEASON (leakage-safe). Keyed on TEAM_ID.

Usage: python scripts/100_synergy_matchup.py
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TG = ROOT / "data" / "parquet" / "team_games.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
SYN = ROOT / "data" / "parquet" / "team_synergy.parquet"

ORDER = [f"{y}-{str(y + 1)[2:]}" for y in range(2013, 2026)]
PREV = {s: ORDER[i - 1] for i, s in enumerate(ORDER) if i}
HCA = 1.5   # home offensive-rating bump (same for both methods; aids absolute MAE)


def mae(a, b):
    return float(np.mean(np.abs(np.array(a) - np.array(b))))


def main() -> None:
    tg = pd.read_parquet(TG)
    tg = tg[tg.SEASON_TYPE == "Regular Season"]
    off_rtg, def_rtg = {}, {}
    for (tid, s), g in tg.groupby(["TEAM_ID", "SEASON"]):
        off_rtg[(tid, s)] = g.offensiveRating.mean()
        def_rtg[(tid, s)] = g.defensiveRating.mean()
    lg_def_rtg = {s: np.mean([v for (t, ss), v in def_rtg.items() if ss == s]) for s in ORDER}
    game_off = {(r.GAME_ID, r.TEAM_ID): r.offensiveRating for r in tg.itertuples()}

    games = pd.read_parquet(GAMES)
    home_away = {}
    for r in games.itertuples():
        home_away[r.GAME_ID] = (r.HOME_TEAM_ID, r.AWAY_TEAM_ID)

    syn = pd.read_parquet(SYN)
    freq, offppp, defppp = {}, {}, {}
    for r in syn.itertuples():
        if r.side == "offensive":
            freq[(r.TEAM_ID, r.SEASON, r.play_type)] = r.poss_pct if not np.isnan(r.poss_pct) else 0.0
            offppp[(r.TEAM_ID, r.SEASON, r.play_type)] = r.ppp
        else:
            defppp[(r.TEAM_ID, r.SEASON, r.play_type)] = r.ppp
    types = sorted(syn.play_type.unique())
    lg_defppp = {}
    for s in syn.SEASON.unique():
        for pt in types:
            vals = [defppp[(t, s, pt)] for t in {k[0] for k in defppp}
                    if (t, s, pt) in defppp and not np.isnan(defppp[(t, s, pt)])]
            if vals:
                lg_defppp[(s, pt)] = np.mean(vals)

    rows = []
    for (gid, tid), actual in game_off.items():
        ha = home_away.get(gid)
        if ha is None or gid[:3] != "002":   # RS game ids
            pass
        tgrow = tg[(tg.GAME_ID == gid) & (tg.TEAM_ID == tid)]
        if not len(tgrow):
            continue
        season = tgrow.SEASON.iloc[0]
        ps = PREV.get(season)
        if ps is None or ha is None:
            continue
        opp = ha[1] if tid == ha[0] else ha[0]
        is_home = tid == ha[0]
        a_base = off_rtg.get((tid, ps))
        b_def = def_rtg.get((opp, ps))
        if a_base is None or b_def is None:
            continue
        home_adj = HCA if is_home else -HCA
        pred_td = a_base + (b_def - lg_def_rtg[ps]) + home_adj

        # bottom-up opponent adjustment: play-type-weighted defensive deviation
        num = den = 0.0
        for pt in types:
            fa = freq.get((tid, ps, pt), 0.0)
            bd = defppp.get((opp, ps, pt)); lg = lg_defppp.get((ps, pt))
            if fa and bd is not None and lg is not None and not np.isnan(bd):
                num += fa * (bd - lg); den += fa
        if den == 0:
            continue
        bu_adj = 100 * num / den
        pred_bu = a_base + bu_adj + home_adj

        rows.append((actual, pred_td, pred_bu, a_base + home_adj))

    d = pd.DataFrame(rows, columns=["actual", "td", "bu", "base"])
    print(f"Bottom-up matchup backtest — {len(d):,} team-games (prior-season profiles)\n")
    print(f"{'method':<38}{'MAE':>9}{'corr':>9}")
    for name, col in [("baseline (offense only, no opp adj)", "base"),
                      ("top-down (aggregate def rating)", "td"),
                      ("bottom-up (play-type def matchup)", "bu")]:
        print(f"{name:<38}{mae(d.actual, d[col]):>9.3f}{np.corrcoef(d[col], d.actual)[0,1]:>9.3f}")

    # does bottom-up add signal beyond top-down? regress actual on both
    A = np.column_stack([np.ones(len(d)), d.td - d.base, d.bu - d.base])
    b, *_ = np.linalg.lstsq(A, d.actual - d.base, rcond=None)
    print(f"\nAdjustment terms regressed on actual residual (weight = which matters):")
    print(f"  top-down adj weight  {b[1]:+.3f}")
    print(f"  bottom-up adj weight {b[2]:+.3f}   (higher = more real matchup signal)")

    # 50/50 blend check
    blend = d.base + 0.5 * (d.td - d.base) + 0.5 * (d.bu - d.base)
    print(f"\n  50/50 blend MAE {mae(d.actual, blend):.3f}  corr {np.corrcoef(blend, d.actual)[0,1]:.3f}")


if __name__ == "__main__":
    main()
