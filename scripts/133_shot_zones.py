"""
Per-player shot-zone profiles — where a player shoots from, and how well.

The simulator gives every player a single FG2_PCT and a single FG3_PCT. That
makes a rim-running centre and a mid-range shooter with equal two-point accuracy
simulate identically, and treats a corner three the same as one from the top of
the arc. The league splits are not close:

    rim      25.4% of shots   67.8% FG   1.357 pts/shot
    corner3  11.0%            38.6%      1.158
    arc3     31.1%            35.1%      1.052
    paint    24.5%            44.7%      0.894
    mid       8.1%            41.5%      0.829

A 64% spread in shot value between rim and mid-range is invisible to the engine
today. Modelling the ZONE MIX also changes what defence means: a good defence
pushes shots away from the rim, which the current "multiply the make probability"
adjustment cannot represent.

Zones use the shot coordinates already parsed into pbp (SHOT_X/SHOT_Y give the
corner-vs-arc split; a corner three is inside |y| < 95).

Two levels of shrinkage, because zone samples get thin fast:
  - zone SHARES shrink toward the player's positional average
  - zone FG% shrinks toward the league rate for that zone, weighted by attempts

Everything is Marcel-projected from PRIOR seasons, so it stays leakage-safe and
drops into the simulator exactly like the other rate columns.

Output: data/parquet/shot_zones.parquet  (one row per player-season)
Usage: python scripts/133_shot_zones.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PBP = ROOT / "data" / "parquet" / "pbp"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
OUT = ROOT / "data" / "parquet" / "shot_zones.parquet"

ZONES = ["rim", "paint", "mid", "corner3", "arc3"]
ZONE_PTS = {"rim": 2, "paint": 2, "mid": 2, "corner3": 3, "arc3": 3}
K_SHARE = 60.0     # phantom attempts pulling a share toward the positional norm
K_FG = 90.0        # phantom attempts pulling a zone FG% toward league


def classify(df: pd.DataFrame) -> pd.Series:
    d, x, y, v = df.SHOT_DISTANCE, df.SHOT_X, df.SHOT_Y, df.SHOT_VALUE
    return np.where(v == 3, np.where(y.abs() < 95, "corner3", "arc3"),
                    np.where(d < 4, "rim", np.where(d < 14, "paint", "mid")))


def main() -> None:
    seasons = sorted(p.stem for p in PBP.glob("*.parquet"))
    frames = []
    for s in seasons:
        p = pd.read_parquet(PBP / f"{s}.parquet",
                            columns=["SEASON", "PLAYER_ID", "IS_FIELD_GOAL", "SHOT_VALUE",
                                     "SHOT_RESULT", "SHOT_DISTANCE", "SHOT_X", "SHOT_Y"])
        f = p[(p.IS_FIELD_GOAL == 1) & p.SHOT_VALUE.isin([2, 3])
              & p.SHOT_RESULT.isin(["Made", "Missed"]) & p.SHOT_DISTANCE.notna()
              & p.SHOT_X.notna() & p.SHOT_Y.notna() & p.PLAYER_ID.notna()].copy()
        f["zone"] = classify(f)
        f["made"] = (f.SHOT_RESULT == "Made").astype(int)
        frames.append(f[["SEASON", "PLAYER_ID", "zone", "made"]])
        print(f"  {s}: {len(f):,} located shots", flush=True)
    sh = pd.concat(frames, ignore_index=True)
    sh["PLAYER_ID"] = sh.PLAYER_ID.astype("int64")

    lg = sh.groupby(["SEASON", "zone"]).made.agg(["size", "mean"]).reset_index()
    lg_share = (lg.groupby("SEASON")["size"].apply(lambda s: s / s.sum())
                .reset_index(level=0, drop=True))
    lg["share"] = lg_share
    lg_fg = {(r.SEASON, r.zone): r["mean"] for _, r in lg.iterrows()}
    lg_sh = {(r.SEASON, r.zone): r.share for _, r in lg.iterrows()}

    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "POS"])
    def bucket(p):
        if not isinstance(p, str):
            return "F"
        for k in ("C", "G", "F"):
            if k in p:
                return k
        return "F"
    pos = {(r.PLAYER_ID, r.SEASON): bucket(r.POS) for r in ps.itertuples()}
    sh["pos"] = [pos.get((p, s), "F") for p, s in zip(sh.PLAYER_ID, sh.SEASON)]

    # positional zone mix — the prior a thin player-season shrinks toward
    pz = sh.groupby(["SEASON", "pos", "zone"]).size().rename("n").reset_index()
    pz["tot"] = pz.groupby(["SEASON", "pos"]).n.transform("sum")
    pz["share"] = pz.n / pz.tot
    pos_share = {(r.SEASON, r.pos, r.zone): r.share for _, r in pz.iterrows()}

    g = sh.groupby(["PLAYER_ID", "SEASON", "pos", "zone"]).made.agg(
        att="size", made="sum").reset_index()
    rows = []
    for (pid, season, pb), d in g.groupby(["PLAYER_ID", "SEASON", "pos"]):
        tot = d.att.sum()
        if tot < 40:
            continue
        rec = {"PLAYER_ID": pid, "SEASON": season, "FGA": int(tot)}
        for z in ZONES:
            r = d[d.zone == z]
            att = int(r.att.iloc[0]) if len(r) else 0
            mad = int(r.made.iloc[0]) if len(r) else 0
            prior_sh = pos_share.get((season, pb, z), lg_sh.get((season, z), 0.2))
            rec[f"sh_{z}"] = (att + K_SHARE * prior_sh) / (tot + K_SHARE)
            lgf = lg_fg.get((season, z), 0.45)
            rec[f"fg_{z}"] = (mad + K_FG * lgf) / (att + K_FG)
        # renormalise shares after shrinkage
        tot_sh = sum(rec[f"sh_{z}"] for z in ZONES)
        for z in ZONES:
            rec[f"sh_{z}"] /= tot_sh
        rows.append(rec)

    out = pd.DataFrame(rows)
    out.to_parquet(OUT, index=False)
    print(f"\nshot_zones: {len(out):,} player-seasons -> {OUT}")

    q = out[out.SEASON == "2024-25"]
    nm = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "PLAYER"])
    nm = {r.PLAYER_ID: r.PLAYER for r in nm[nm.SEASON == "2024-25"].itertuples()}
    q = q[q.FGA >= 500].copy()
    q["xpts"] = sum(q[f"sh_{z}"] * q[f"fg_{z}"] * ZONE_PTS[z] for z in ZONES)
    print("\nFace check — 2024-25, most rim-reliant (>=500 FGA):")
    for r in q.nlargest(4, "sh_rim").itertuples():
        print(f"  {nm.get(r.PLAYER_ID, r.PLAYER_ID)!s:<24} rim {r.sh_rim*100:.0f}% "
              f"arc3 {r.sh_arc3*100:.0f}%   xPTS/shot {r.xpts:.3f}")
    print("\nMost three-reliant:")
    for r in q.nlargest(4, "sh_arc3").itertuples():
        print(f"  {nm.get(r.PLAYER_ID, r.PLAYER_ID)!s:<24} rim {r.sh_rim*100:.0f}% "
              f"arc3 {r.sh_arc3*100:.0f}%   xPTS/shot {r.xpts:.3f}")
    print("\nHighest expected points per shot:")
    for r in q.nlargest(4, "xpts").itertuples():
        print(f"  {nm.get(r.PLAYER_ID, r.PLAYER_ID)!s:<24} rim {r.sh_rim*100:.0f}% "
              f"corner3 {r.sh_corner3*100:.0f}%   xPTS/shot {r.xpts:.3f}")


if __name__ == "__main__":
    main()
