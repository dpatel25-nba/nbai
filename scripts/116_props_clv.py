"""
Props opening-line edge + CLV — the test that matters.

Game closing lines were efficient; our edge lived in OPENING lines (CLV +0.68).
Props open even softer. This tests, on player points:
  1. opening-line edge — bet our over/under pick vs the OPENING line, win% + ROI
  2. CLV — do we beat the CLOSING prop line? (line moves toward our pick = real edge)
  3. concentration in absence-driven spots (vacated)

Reads the open + close snapshots (data/raw/props_odds/*_open/_close.json).
Usage: python scripts/116_props_clv.py
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
    for j in (" jr", " iii", " ii", " iv", ".", "'"):
        n = n.replace(j, "")
    return n.strip()


def profit(price, won):
    if not (abs(price) >= 100):
        return np.nan
    return (price / 100 if price > 0 else 100 / -price) if won else -1.0


def parse(tag):
    rows = []
    for f in glob.glob(str(RAW / f"*_player_points_{tag}.json")):
        gid = Path(f).stem.replace(f"_player_points_{tag}", "")
        data = json.loads(Path(f).read_text()).get("data", {})
        agg = defaultdict(lambda: {"l": [], "op": [], "up": []})
        for bk in data.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk.get("key") != "player_points":
                    continue
                for o in mk.get("outcomes", []):
                    a = agg[norm(o.get("description"))]
                    a["l"].append(o.get("point"))
                    (a["op"] if o.get("name") == "Over" else a["up"]).append(o.get("price"))
        for p, a in agg.items():
            if a["l"]:
                rows.append({"GAME_ID": gid, "pname": p, f"{tag}_line": float(np.median(a["l"])),
                             f"{tag}_op": float(np.median(a["op"])) if a["op"] else np.nan,
                             f"{tag}_up": float(np.median(a["up"])) if a["up"] else np.nan})
    return pd.DataFrame(rows)


def main() -> None:
    op, cl = parse("open"), parse("close")
    if not len(op) or not len(cl):
        print("Need both open and close snapshots — pull still warming up."); return
    d = op.merge(cl, on=["GAME_ID", "pname"], how="inner")
    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "PLAYER"])
    nm = {norm(r.PLAYER): r.PLAYER_ID for r in ps[ps.SEASON == "2024-25"].itertuples()}
    d["PLAYER_ID"] = d.pname.map(nm)
    d = d.dropna(subset=["PLAYER_ID"]); d["PLAYER_ID"] = d.PLAYER_ID.astype(int)
    pred = pd.read_parquet(PRED)
    pred = pred[pred.SEASON == "2024-25"][["GAME_ID", "PLAYER_ID", "pred_points", "points"]]
    d = d.merge(pred, on=["GAME_ID", "PLAYER_ID"], how="inner").dropna(subset=["pred_points"])
    print(f"Props CLV — {len(d):,} player-games with open+close lines + our projection\n")
    if not len(d):
        return

    # (1) opening-line edge: pick vs OPEN line
    dd = d[d.points != d.open_line].copy()
    dd["over"] = dd.pred_points > dd.open_line
    dd["won"] = (dd.over & (dd.points > dd.open_line)) | (~dd.over & (dd.points < dd.open_line))
    dd["price"] = np.where(dd.over, dd.open_op, dd.open_up)
    dd["edge"] = (dd.pred_points - dd.open_line).abs()
    print("(1) Beat the OPENING prop line:")
    for lbl, q in [("all", 0.0), ("top 50%", 0.5), ("top 25%", 0.75), ("top 10%", 0.9)]:
        m = dd.edge >= dd.edge.quantile(q)
        roi = np.nanmean([profit(p, w) for p, w in zip(dd.price[m], dd.won[m])]) * 100
        print(f"    {lbl:<10} win {dd.won[m].mean()*100:5.2f}%   ROI {roi:+5.2f}%   ({m.sum():,})")

    # (2) CLV: does the CLOSING line move toward our pick?
    c = d.copy()
    c["over"] = c.pred_points > c.open_line
    # if we bet OVER at open_line, favorable = close_line rises (we got the lower number)
    c["clv"] = np.where(c.over, c.close_line - c.open_line, c.open_line - c.close_line)
    c["edge"] = (c.pred_points - c.open_line).abs()
    print(f"\n(2) Closing Line Value (line moves toward our pick after open):")
    print(f"    mean CLV {c.clv.mean():+.3f} pts   positive {int((c.clv>0).mean()*100)}%   ({len(c):,} bets)")
    for lbl, q in [("top 50%", 0.5), ("top 25%", 0.75), ("top 10%", 0.9)]:
        m = c.edge >= c.edge.quantile(q)
        print(f"    {lbl:<10} mean CLV {c.clv[m].mean():+.3f}  ({m.sum():,})")

    # (3) absence-driven spots
    feat = pd.read_parquet(FEAT)[["GAME_ID", "PLAYER_ID", "vacated_min"]]
    c = c.merge(feat, on=["GAME_ID", "PLAYER_ID"], how="left")
    hi = c[c.vacated_min >= c.vacated_min.quantile(0.8)]
    print(f"\n(3) High teammate-absence (top-20% vacated): mean CLV {hi.clv.mean():+.3f}  ({len(hi):,})")


if __name__ == "__main__":
    main()
