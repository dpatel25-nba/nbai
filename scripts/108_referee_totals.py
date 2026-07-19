"""
Do referee crews predict game totals beyond the line? (the edge test)

Each ref gets a totals tendency = how much scoring happens in games they work,
vs league average, computed LEAVE-ONE-OUT (exclude the game being scored, no
leakage). A game's crew tendency = mean of its refs' tendencies. Then:
  1. does crew tendency correlate with actual total? (do refs matter at all)
  2. does it predict act_total − open_total? (is that scoring UNPRICED = edge)
  3. betting: over when crew is high-scoring, vs the opening total.

Runs on whatever refs are pulled so far (reads data/raw/refs/*.json).
Usage: python scripts/108_referee_totals.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "refs"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
LINES = ROOT / "data" / "parquet" / "game_lines.parquet"
SIM = ROOT / "data" / "features" / "sim_mode1_predictions.parquet"
BE = 52.38
MIN_G = 20   # min games for a ref to have a usable tendency


def main() -> None:
    # crew per game from raw files
    crew = defaultdict(list)
    for f in RAW.glob("*.json"):
        for o in json.loads(f.read_text()):
            if o.get("OFFICIAL_ID") is not None:
                crew[f.stem].append(o["OFFICIAL_ID"])
    if not crew:
        print("No referee files yet — pull still warming up."); return

    games = pd.read_parquet(GAMES, columns=["GAME_ID", "HOME_PTS", "AWAY_PTS"])
    tot = {r.GAME_ID: r.HOME_PTS + r.AWAY_PTS for r in games.itertuples()}
    crew = {g: refs for g, refs in crew.items() if g in tot}
    league_mean = np.mean([tot[g] for g in crew])

    # per-ref totals (for leave-one-out tendency)
    ref_sum = defaultdict(float); ref_cnt = defaultdict(int)
    for g, refs in crew.items():
        for r in refs:
            ref_sum[r] += tot[g]; ref_cnt[r] += 1

    rows = []
    for g, refs in crew.items():
        devs = []
        for r in refs:
            if ref_cnt[r] > MIN_G:
                loo = (ref_sum[r] - tot[g]) / (ref_cnt[r] - 1)   # leave-one-out mean
                devs.append(loo - league_mean)
        if devs:
            rows.append({"GAME_ID": g, "act_total": tot[g], "crew_dev": float(np.mean(devs))})
    d = pd.DataFrame(rows)
    print(f"Referee-totals test — {len(d):,} games, {len(ref_cnt)} refs "
          f"(league avg total {league_mean:.1f})\n")

    # (1) crew tendency vs actual total
    print(f"corr(crew tendency, actual total)        = "
          f"{np.corrcoef(d.crew_dev, d.act_total)[0,1]:+.3f}   (do refs move scoring?)")

    # (2)/(3) vs the opening line
    lines = pd.read_parquet(LINES)[["GAME_ID", "open_total"]]
    sim = pd.read_parquet(SIM)[["GAME_ID", "pred_total"]]
    e = d.merge(lines, on="GAME_ID").merge(sim, on="GAME_ID")
    e = e[e.open_total.notna() & (e.act_total != e.open_total)].copy()
    if len(e):
        e["resid"] = e.act_total - e.open_total
        print(f"corr(crew tendency, total − opening)     = "
              f"{np.corrcoef(e.crew_dev, e.resid)[0,1]:+.3f}   (is it UNPRICED = edge?)")
        print(f"\nBet OVER when crew is high-scoring (vs opening total), {len(e):,} games:")
        for lbl, qq in [("all", 0.0), ("top 40% crew", 0.6), ("top 20% crew", 0.8),
                        ("top 10% crew", 0.9)]:
            m = e.crew_dev >= e.crew_dev.quantile(qq)
            wr = (e.resid[m] > 0).mean() * 100
            print(f"  {lbl:<16} {wr:5.2f}%  ({m.sum():,})  {'EDGE' if wr > BE else ''}")
        # does adding ref tendency to sim's total sharpen the edge?
        for name, col in [("sim only", e.pred_total),
                          ("sim + ref crew", e.pred_total + e.crew_dev)]:
            edge = (col - e.open_total).abs()
            win = ((col > e.open_total) & (e.act_total > e.open_total)) | \
                  ((col < e.open_total) & (e.act_total < e.open_total))
            m = edge >= edge.quantile(0.8)
            print(f"  {name:<16} top20% edge {win[m].mean()*100:.2f}%  ({m.sum():,})")


if __name__ == "__main__":
    main()
