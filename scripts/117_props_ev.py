"""
Rigorous props edge — expected value vs the de-vigged market, not just win%.

Win-rate/ROI ignore edge magnitude and the market's own uncertainty. The correct
test: turn our projection into a PROBABILITY of going over the line (mean +
conditional σ), compare to the market's DE-VIGGED fair probability, and bet only
positive-EV sides. Report EV, ROI, and a flat-stake bankroll curve — honest
(walk-forward σ, edge thresholds reported, no in-sample cherry-picking).

σ(pred) is fit on PRIOR seasons' projection residuals (leakage-safe).
Reads closing prop lines. Usage: python scripts/117_props_ev.py
"""

from __future__ import annotations

import glob
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "props_odds"
PRED = ROOT / "data" / "features" / "props_predictions.parquet"
FEAT = ROOT / "data" / "parquet" / "props_features.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"


def nm(x):
    n = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
    for j in (" jr", " iii", " ii", " iv", ".", "'"):
        n = n.replace(j, "")
    return n.strip()


def imp(price):
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def parse_close():
    rows = []
    for f in glob.glob(str(RAW / "*_player_points_close.json")):
        gid = Path(f).stem.replace("_player_points_close", "")
        data = json.loads(Path(f).read_text()).get("data", {})
        agg = defaultdict(lambda: {"l": [], "op": [], "up": []})
        for bk in data.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk.get("key") != "player_points":
                    continue
                for o in mk.get("outcomes", []):
                    a = agg[nm(o.get("description"))]
                    a["l"].append(o.get("point"))
                    (a["op"] if o.get("name") == "Over" else a["up"]).append(o.get("price"))
        for p, a in agg.items():
            if a["l"] and a["op"] and a["up"]:
                rows.append({"GAME_ID": gid, "pname": p, "line": float(np.median(a["l"])),
                             "op": float(np.median(a["op"])), "up": float(np.median(a["up"]))})
    return pd.DataFrame(rows)


def main() -> None:
    props = parse_close()
    if not len(props):
        print("No closing prop data yet."); return

    # sigma(pred) from PRIOR-season residuals (leakage-safe): std of (points-pred) by pred bin
    pred_all = pd.read_parquet(PRED).dropna(subset=["pred_points"])
    train = pred_all[pred_all.SEASON < "2024-25"].copy()
    train["b"] = (train.pred_points / 3).round()
    sig = train.groupby("b").apply(lambda g: (g.points - g.pred_points).std(), include_groups=False)
    # linear fallback sigma = a + b*pred
    A = np.column_stack([np.ones(len(train)), train.pred_points])
    coef, *_ = np.linalg.lstsq(A, (train.points - train.pred_points).abs() * 1.2533, rcond=None)  # E|resid|*1.25≈sigma

    def sigma(pred):
        s = sig.get(round(pred / 3), np.nan)
        return s if not np.isnan(s) else max(3.0, coef[0] + coef[1] * pred)

    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "PLAYER"])
    name = {nm(r.PLAYER): r.PLAYER_ID for r in ps[ps.SEASON == "2024-25"].itertuples()}
    props["PLAYER_ID"] = props.pname.map(name)
    props = props.dropna(subset=["PLAYER_ID"]); props["PLAYER_ID"] = props.PLAYER_ID.astype(int)
    cur = pred_all[pred_all.SEASON == "2024-25"][["GAME_ID", "PLAYER_ID", "pred_points", "points"]]
    d = props.merge(cur, on=["GAME_ID", "PLAYER_ID"], how="inner").dropna(subset=["pred_points"])
    d = d[(d.points != d.line) & (d.op.abs() >= 100) & (d.up.abs() >= 100)].copy()  # valid American odds
    print(f"Props EV analysis — {len(d):,} closing prop bets\n")
    if not len(d):
        return

    d["sig"] = d.pred_points.map(sigma)
    d["p_over"] = 1 - norm.cdf((d.line - d.pred_points) / d.sig)      # our prob of over
    d["fair_over"] = [imp(o) / (imp(o) + imp(u)) for o, u in zip(d.op, d.up)]  # de-vigged market
    d["dec_over"] = np.where(d.op > 0, d.op / 100 + 1, 100 / -d.op + 1)
    d["dec_under"] = np.where(d.up > 0, d.up / 100 + 1, 100 / -d.up + 1)
    # EV of each side using OUR prob at the actual price
    d["ev_over"] = d.p_over * d.dec_over - 1
    d["ev_under"] = (1 - d.p_over) * d.dec_under - 1
    d["bet_over"] = d.ev_over > d.ev_under
    d["ev"] = np.where(d.bet_over, d.ev_over, d.ev_under)
    d["won"] = (d.bet_over & (d.points > d.line)) | (~d.bet_over & (d.points < d.line))
    d["dec"] = np.where(d.bet_over, d.dec_over, d.dec_under)
    d["pnl"] = np.where(d.won, d.dec - 1, -1.0)
    d["prob_edge"] = np.where(d.bet_over, d.p_over - d.fair_over, d.fair_over - d.p_over)

    print("Bet positive-EV side; ROI = realized, by our modeled edge over the fair line:")
    print(f"  {'edge bucket':<20}{'bets':>7}{'model EV':>10}{'actual ROI':>12}{'win%':>8}")
    for lbl, lo, hi in [("all positive-EV", 0.0, 1), ("edge > 2%", 0.02, 1),
                        ("edge > 4%", 0.04, 1), ("edge > 6%", 0.06, 1)]:
        m = (d.ev > 0) & (d.prob_edge >= lo)
        if m.sum():
            print(f"  {lbl:<20}{m.sum():>7}{d.ev[m].mean()*100:>9.2f}%{d.pnl[m].mean()*100:>11.2f}%"
                  f"{d.won[m].mean()*100:>7.1f}%")

    # absence-driven spots
    feat = pd.read_parquet(FEAT)[["GAME_ID", "PLAYER_ID", "vacated_min"]]
    d = d.merge(feat, on=["GAME_ID", "PLAYER_ID"], how="left")
    hi = d[(d.ev > 0) & (d.vacated_min >= d.vacated_min.quantile(0.8))]
    if len(hi):
        print(f"\n  High teammate-absence + positive-EV: ROI {hi.pnl.mean()*100:+.2f}%  "
              f"win {hi.won.mean()*100:.1f}%  ({len(hi):,} bets)")


if __name__ == "__main__":
    main()
