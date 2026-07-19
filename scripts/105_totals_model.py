"""
Dedicated game-TOTALS model — sharpen the edge we found vs opening lines.

The opening-total edge (script 102 re-parse) is driven by our total prediction.
A better total prediction → bigger, more accurate disagreements with the soft
opening line → more edge. Build a walk-forward GBM on rolling team scoring /
defense / pace / rest, compare its total MAE to the simple sim_mode1 baseline,
then RE-TEST the opening-line betting edge with the sharper number.

Usage: python scripts/105_totals_model.py
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "data" / "parquet" / "game_logs.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
LINES = ROOT / "data" / "parquet" / "game_lines.parquet"
SIM = ROOT / "data" / "features" / "sim_mode1_predictions.parquet"
BE = 52.38


def gbm():
    return HistGradientBoostingRegressor(max_iter=500, learning_rate=0.04,
                                         max_leaf_nodes=31, l2_regularization=1.0, random_state=7)


def build_features():
    lg = pd.read_parquet(LOGS, columns=["GAME_ID", "TEAM_ID", "GAME_DATE", "SEASON",
                                        "SEASON_TYPE", "PTS", "FGA", "FTA", "OREB", "TOV"])
    lg = lg[lg.SEASON_TYPE == "Regular Season"].copy()
    # opponent points per team-game (for points-allowed)
    m = lg.merge(lg, on="GAME_ID", suffixes=("", "_o"))
    m = m[m.TEAM_ID != m.TEAM_ID_o]
    m["poss"] = m.FGA + 0.44 * m.FTA - m.OREB + m.TOV
    m = m.sort_values("GAME_DATE")

    off = defaultdict(lambda: deque(maxlen=10)); dfn = defaultdict(lambda: deque(maxlen=10))
    pace = defaultdict(lambda: deque(maxlen=10)); last = {}; prev_s = {}
    rows = {}
    for r in m.itertuples():
        t = r.TEAM_ID
        if prev_s.get(t) != r.SEASON:
            off[t].clear(); dfn[t].clear(); pace[t].clear(); prev_s[t] = r.SEASON
        o = np.mean(off[t]) if off[t] else 112.0
        d = np.mean(dfn[t]) if dfn[t] else 112.0
        p = np.mean(pace[t]) if pace[t] else 99.0
        rest = (r.GAME_DATE - last[t]).days if t in last else 3
        rows[(r.GAME_ID, t)] = {"off": o, "def": d, "pace": p,
                                "rest": min(rest, 5), "b2b": int(rest == 1)}
        off[t].append(r.PTS); dfn[t].append(r.PTS_o); pace[t].append(r.poss)
        last[t] = r.GAME_DATE
    return rows


def main() -> None:
    feat = build_features()
    games = pd.read_parquet(GAMES)
    games = games[games.SEASON_TYPE == "Regular Season"]
    rows = []
    for g in games.itertuples():
        fh, fa = feat.get((g.GAME_ID, g.HOME_TEAM_ID)), feat.get((g.GAME_ID, g.AWAY_TEAM_ID))
        if fh is None or fa is None:
            continue
        rows.append({"GAME_ID": g.GAME_ID, "SEASON": g.SEASON,
                     "total": g.HOME_PTS + g.AWAY_PTS,
                     "h_off": fh["off"], "h_def": fh["def"], "h_pace": fh["pace"],
                     "a_off": fa["off"], "a_def": fa["def"], "a_pace": fa["pace"],
                     "h_rest": fh["rest"], "a_rest": fa["rest"],
                     "h_b2b": fh["b2b"], "a_b2b": fa["b2b"],
                     "pace_sum": fh["pace"] + fa["pace"],
                     "naive": (fh["off"] + fa["def"] + fa["off"] + fh["def"]) / 2})
    d = pd.DataFrame(rows)
    FEATS = ["h_off", "h_def", "h_pace", "a_off", "a_def", "a_pace",
             "h_rest", "a_rest", "h_b2b", "a_b2b", "pace_sum", "naive"]
    season = d.SEASON
    seasons = sorted(season.unique())
    pred = np.full(len(d), np.nan)
    for T in seasons[2:]:
        tr, te = (season < T).to_numpy(), (season == T).to_numpy()
        if te.sum():
            mdl = gbm(); mdl.fit(d[FEATS][tr], d.total.to_numpy()[tr])
            pred[te] = mdl.predict(d[FEATS][te])
    d["pred_total_v2"] = pred
    test = season.isin(seasons[2:]).to_numpy() & ~np.isnan(pred)
    print(f"Totals model — {test.sum():,} test games")
    print(f"  new model MAE   {mean_absolute_error(d.total.to_numpy()[test], pred[test]):.3f}")
    print(f"  naive baseline  {mean_absolute_error(d.total.to_numpy()[test], d.naive.to_numpy()[test]):.3f}")

    # --- re-test the opening-line edge with the sharper total ---
    lines = pd.read_parquet(LINES)
    sim = pd.read_parquet(SIM)[["GAME_ID", "pred_total", "act_total"]]
    e = d.merge(lines, on="GAME_ID").merge(sim, on="GAME_ID")
    e = e[e.open_total.notna() & e.pred_total_v2.notna() & (e.act_total != e.open_total)].copy()

    print(f"\nOpening-total edge, top-20% disagreement ({len(e):,} games w/ opening line):")
    for name, col in [("sim_mode1 (old)", "pred_total"), ("totals model v2 (new)", "pred_total_v2")]:
        edge = (e[col] - e.open_total).abs()
        win = ((e[col] > e.open_total) & (e.act_total > e.open_total)) | \
              ((e[col] < e.open_total) & (e.act_total < e.open_total))
        for lbl, qq in [("all", 0.0), ("top20%", 0.8), ("top10%", 0.9), ("top5%", 0.95)]:
            mask = edge >= edge.quantile(qq)
            wr = win[mask].mean() * 100
            if lbl == "top20%":
                print(f"  {name:<24} {wr:5.2f}%  ({mask.sum():,} bets)  {'PROFIT' if wr > BE else ''}")
    # recent-only, new model
    rec = e[e.SEASON_x >= "2019-20"]
    edge = (rec.pred_total_v2 - rec.open_total).abs()
    win = ((rec.pred_total_v2 > rec.open_total) & (rec.act_total > rec.open_total)) | \
          ((rec.pred_total_v2 < rec.open_total) & (rec.act_total < rec.open_total))
    mask = edge >= edge.quantile(0.8)
    print(f"\n  v2 model, 2019-20+ top20%: {win[mask].mean()*100:.2f}%  ({mask.sum():,} bets)")


if __name__ == "__main__":
    main()
