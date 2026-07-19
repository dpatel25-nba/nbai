"""
Shot-quality / expected-points (xPTS) model — v2, with x/y location + shot type.

Upgrade over v1 (distance + 2P/3P only): a gradient-boosted model over shot
coordinates (x, y), distance, point value, and shot type (layup/dunk/jumper/…).
This captures that a corner 3 is easier than an above-the-break 3, a dunk easier
than a floater, etc. xPTS = P(make) * shot_value; a player's points-over-expected
(POE) is how much they out-score an average player on the *same* shots.

Still location-and-type only — no per-shot defender (not public). Defender/contest
context layers on later via aggregate matchup data (2017-18+).

Output: player_shot_quality.parquet (per player-season POE). Prints a fair
held-out comparison vs the v1 distance-only baseline.

Usage: python scripts/25_shot_quality.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parents[1]
PBP = ROOT / "data" / "parquet" / "pbp"
OUT = ROOT / "data" / "parquet" / "player_shot_quality.parquet"


def shot_type(s: str) -> str:
    s = str(s).lower()
    for key, grp in [("dunk", "Dunk"), ("layup", "Layup"), ("hook", "Hook"),
                     ("float", "Floater"), ("fadeaway", "Fadeaway"),
                     ("step back", "StepBack"), ("pull", "Pullup"),
                     ("bank", "Bank")]:
        if key in s:
            return grp
    return "Jumper"


def main() -> None:
    pbp = pd.read_parquet(PBP, columns=["SEASON", "PLAYER_ID", "PLAYER_NAME",
                                        "IS_FIELD_GOAL", "SHOT_VALUE", "SHOT_RESULT",
                                        "SHOT_DISTANCE", "SHOT_X", "SHOT_Y", "SUB_TYPE"])
    fga = pbp[(pbp["IS_FIELD_GOAL"] == 1) & pbp["SHOT_VALUE"].isin([2, 3])
              & pbp["SHOT_RESULT"].isin(["Made", "Missed"])
              & pbp["SHOT_DISTANCE"].notna() & pbp["SHOT_X"].notna()
              & pbp["SHOT_Y"].notna()].copy()
    fga["made"] = (fga["SHOT_RESULT"] == "Made").astype(int)
    fga["pts"] = fga["made"] * fga["SHOT_VALUE"]
    fga["stype"] = fga["SUB_TYPE"].map(shot_type)
    print(f"Field-goal attempts: {len(fga):,}  |  shot types: {fga.stype.value_counts().to_dict()}")

    # feature matrix: coords + distance + value + one-hot shot type
    types = pd.get_dummies(fga["stype"], prefix="t")
    feat_cols = ["SHOT_X", "SHOT_Y", "SHOT_DISTANCE", "SHOT_VALUE"] + list(types.columns)
    X = pd.concat([fga[["SHOT_X", "SHOT_Y", "SHOT_DISTANCE", "SHOT_VALUE"]].reset_index(drop=True),
                   types.reset_index(drop=True)], axis=1)
    y = fga["made"].to_numpy()

    # --- fair held-out comparison: v2 GBM vs v1 distance-only ---
    Xtr, Xte, ytr, yte, itr, ite = train_test_split(
        X, y, np.arange(len(fga)), test_size=0.2, random_state=7)
    gbm = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08,
                                         max_leaf_nodes=31, random_state=7)
    gbm.fit(Xtr, ytr)
    ll_v2 = log_loss(yte, gbm.predict_proba(Xte)[:, 1])

    # v1 baseline: empirical make% by (distance bin, value), fit on train only
    tr = fga.iloc[itr]
    tr_cell = tr.assign(dbin=tr.SHOT_DISTANCE.clip(0, 35).round().astype(int)) \
        .groupby(["dbin", "SHOT_VALUE"])["made"].mean()
    te = fga.iloc[ite].assign(dbin=fga.iloc[ite].SHOT_DISTANCE.clip(0, 35).round().astype(int))
    p_v1 = te.apply(lambda r: tr_cell.get((r.dbin, r.SHOT_VALUE), ytr.mean()), axis=1).clip(1e-6, 1 - 1e-6)
    ll_v1 = log_loss(yte, p_v1)
    print(f"\nHeld-out log-loss:  v1 distance-only {ll_v1:.4f}   ->   v2 x/y+type {ll_v2:.4f}"
          f"   ({(1 - ll_v2 / ll_v1) * 100:.1f}% better)")

    # illustrate that location matters: corner 3 vs above-the-break 3 (same distance)
    def make_prob(x, yc, dist, val, st):
        row = {"SHOT_X": x, "SHOT_Y": yc, "SHOT_DISTANCE": dist, "SHOT_VALUE": val}
        for c in types.columns:
            row[c] = 1 if c == f"t_{st}" else 0
        return gbm.predict_proba(pd.DataFrame([row])[feat_cols])[0, 1]
    corner = make_prob(220, 5, 23.8, 3, "Jumper")
    top = make_prob(0, 260, 23.8, 3, "Jumper")
    print(f"  e.g. 23.8-ft 3: corner {corner*100:.1f}% make  vs  above-break {top*100:.1f}% "
          f"(same distance, different spot)")

    # --- final model on all shots -> per-player POE ---
    fga["xmake"] = gbm.predict_proba(X)[:, 1]
    fga["xpts"] = fga["xmake"] * fga["SHOT_VALUE"]
    g = fga.groupby(["PLAYER_ID", "SEASON"]).agg(
        PLAYER=("PLAYER_NAME", "first"), FGA=("made", "size"),
        PTS=("pts", "sum"), xPTS=("xpts", "sum"), FGM=("made", "sum")).reset_index()
    g["POE"] = (g["PTS"] - g["xPTS"]).round(1)
    g["POE_100"] = ((g["PTS"] - g["xPTS"]) / g["FGA"] * 100).round(2)
    g["xEFG"] = (g["xPTS"] / (2 * g["FGA"])).round(3)
    g["EFG"] = (g["PTS"] / (2 * g["FGA"])).round(3)
    g.to_parquet(OUT, index=False)

    print("\nTop 10 shot-makers over expected (min 800 FGA in a season):")
    print(g[g.FGA >= 800].nlargest(10, "POE_100")[
        ["PLAYER", "SEASON", "FGA", "EFG", "xEFG", "POE_100"]].to_string(index=False))


if __name__ == "__main__":
    main()
