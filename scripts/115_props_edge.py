"""
Do our player-points projections beat the prop market? (the paid-data payoff)

Parses the historical points props (data/raw/props_odds/), takes a consensus line
across books, joins to our walk-forward projection (props_predictions) + actuals,
and tests whether we'd beat the market:
  - beat rate: our over/under pick vs the line, vs. the -110-ish breakeven
  - ROI at the actual quoted prices
  - stratified by our disagreement with the line (edge size)
  - concentrated in absence-driven spots? (our validated vacated signal)

Runs on whatever's pulled so far. Usage: python scripts/115_props_edge.py
"""

from __future__ import annotations

import glob
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "props_odds"
PRED = ROOT / "data" / "features" / "props_predictions.parquet"
FEAT = ROOT / "data" / "parquet" / "props_features.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"


def norm(name):
    n = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    for junk in (" jr", " iii", " ii", " iv", ".", "'"):
        n = n.replace(junk, "")
    return n.strip()


def american_prob(p):
    return 100 / (p + 100) if p > 0 else -p / (-p + 100)


def profit(price, won):
    return (price / 100 if price > 0 else 100 / -price) if won else -1.0


def main() -> None:
    files = glob.glob(str(RAW / "*_player_points_close.json"))   # sharp closing line
    # consensus line per (GAME_ID, normalized player name)
    rows = []
    for f in files:
        gid = Path(f).stem.replace("_player_points_close", "")
        data = json.loads(Path(f).read_text()).get("data", {})
        agg = defaultdict(lambda: {"line": [], "op": [], "up": []})
        for bk in data.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk.get("key") != "player_points":
                    continue
                for o in mk.get("outcomes", []):
                    a = agg[norm(o.get("description"))]
                    a["line"].append(o.get("point"))
                    (a["op"] if o.get("name") == "Over" else a["up"]).append(o.get("price"))
        for player, a in agg.items():
            if a["line"] and a["op"] and a["up"]:
                rows.append({"GAME_ID": gid, "pname": player,
                             "line": float(np.median(a["line"])),
                             "over_price": float(np.median(a["op"])),
                             "under_price": float(np.median(a["up"]))})
    props = pd.DataFrame(rows)
    print(f"Parsed props: {len(props):,} player-lines across {props.GAME_ID.nunique():,} games")

    # name -> PLAYER_ID (2024-25 players)
    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "PLAYER"])
    ps = ps[ps.SEASON == "2024-25"]
    nm = {norm(r.PLAYER): r.PLAYER_ID for r in ps.itertuples()}
    props["PLAYER_ID"] = props.pname.map(nm)
    matched = props.PLAYER_ID.notna().mean()
    props = props.dropna(subset=["PLAYER_ID"]); props["PLAYER_ID"] = props.PLAYER_ID.astype(int)

    pred = pd.read_parquet(PRED)
    pred = pred[pred.SEASON == "2024-25"][["GAME_ID", "PLAYER_ID", "pred_points", "points"]]
    d = props.merge(pred, on=["GAME_ID", "PLAYER_ID"], how="inner").dropna(subset=["pred_points"])
    d = d[d.points != d.line].copy()
    print(f"Name match rate: {matched*100:.0f}%   |   {len(d):,} prop bets joined to our projections\n")
    if not len(d):
        print("No overlap yet — pull still warming up."); return

    d["pick_over"] = d.pred_points > d.line
    d["won"] = (d.pick_over & (d.points > d.line)) | (~d.pick_over & (d.points < d.line))
    d["price"] = np.where(d.pick_over, d.over_price, d.under_price)
    d["edge"] = (d.pred_points - d.line).abs()
    d = d[d.price.notna() & (d.price.abs() >= 100)].copy()   # valid American odds only

    print("=== Do we beat the points prop market? ===")
    for lbl, q in [("ALL bets", 0.0), ("top 50% edge", 0.5), ("top 25% edge", 0.75),
                   ("top 10% edge", 0.9)]:
        m = d.edge >= d.edge.quantile(q)
        wr = d.won[m].mean() * 100
        roi = np.mean([profit(p, w) for p, w in zip(d.price[m], d.won[m])]) * 100
        print(f"  {lbl:<16} win {wr:5.2f}%   ROI {roi:+5.2f}%   ({m.sum():,} bets)")

    # concentrated in absence-driven spots? (vacated signal)
    feat = pd.read_parquet(FEAT)[["GAME_ID", "PLAYER_ID", "vacated_min", "vacated_delta"]]
    d = d.merge(feat, on=["GAME_ID", "PLAYER_ID"], how="left")
    hi = d[d.vacated_min >= d.vacated_min.quantile(0.8)]
    print(f"\n  High teammate-absence spots (top-20% vacated_min): win {hi.won.mean()*100:.2f}%  "
          f"ROI {np.mean([profit(p,w) for p,w in zip(hi.price,hi.won)])*100:+.2f}%  ({len(hi):,} bets)")
    print(f"  (breakeven ≈ 52.4%; our avg line vs our proj lets us pick a side)")


if __name__ == "__main__":
    main()
