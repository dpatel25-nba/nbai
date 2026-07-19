"""
WAR bake-off, defensive side: does tracking rim-protection improve box DBPM?

Box DBPM barely sees defense (known weak spot). The tracking data has the thing
that most drives TEAM defense — RIM PROTECTION (opponent FG% at the rim). Build a
rim-protection value and test whether it (and matchup perimeter defense) adds to
box DBPM for predicting team defensive rating, out-of-sample (LOSO CV).

  rim_val/game = DEF_RIM_FGA · (league_rim_FG% − player_rim_FG%)   (shots saved at rim)

Mirrors script 109's offensive bake-off. Lower team DRtg = better defense.
Usage: python scripts/110_defense_value.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TRK = ROOT / "data" / "parquet" / "player_tracking.parquet"
WAR = ROOT / "data" / "parquet" / "player_seasons_war.parquet"
DQ = ROOT / "data" / "parquet" / "defender_quality_v2.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
TG = ROOT / "data" / "parquet" / "team_games.parquet"

ORDER = [f"{y}-{str(y + 1)[2:]}" for y in range(2013, 2026)]
PREV = {s: ORDER[i - 1] for i, s in enumerate(ORDER) if i}


def loso(d, y, cols, seasons):
    pred = np.full(len(d), np.nan)
    for s in np.unique(seasons):
        tr, te = seasons != s, seasons == s
        A = np.column_stack([np.ones(tr.sum())] + [d[c].to_numpy()[tr] for c in cols])
        b, *_ = np.linalg.lstsq(A, y[tr], rcond=None)
        pred[te] = np.column_stack([np.ones(te.sum())] + [d[c].to_numpy()[te] for c in cols]) @ b
    return np.sqrt(np.mean((y - pred) ** 2)), np.corrcoef(pred, y)[0, 1]


def main() -> None:
    trk = pd.read_parquet(TRK)
    trk = trk[trk.MIN.notna() & (trk.MIN > 0)].copy()
    # league rim FG% per season (volume-weighted)
    lg = (trk.dropna(subset=["DEF_RIM_FG_PCT", "DEF_RIM_FGA"])
             .groupby("SEASON")
             .apply(lambda g: np.average(g.DEF_RIM_FG_PCT, weights=g.DEF_RIM_FGA + 1e-9),
                    include_groups=False).rename("lg_rim").reset_index())
    trk = trk.merge(lg, on="SEASON", how="left")
    trk["rim_val"] = trk.DEF_RIM_FGA.fillna(0) * (trk.lg_rim - trk.DEF_RIM_FG_PCT.fillna(trk.lg_rim))

    war = pd.read_parquet(WAR, columns=["PLAYER_ID", "SEASON", "DBPM"])
    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "TEAM", "MIN"])
    dq = pd.read_parquet(DQ)[["PLAYER_ID", "SEASON", "skill"]]
    m = (trk[["PLAYER_ID", "SEASON", "rim_val"]]
         .merge(ps, on=["PLAYER_ID", "SEASON"])
         .merge(war, on=["PLAYER_ID", "SEASON"], how="left")
         .merge(dq, on=["PLAYER_ID", "SEASON"], how="left"))
    m = m[m.MIN >= 500].copy()
    m["skill"] = m.skill.fillna(0.0)

    tg = pd.read_parquet(TG); tg = tg[tg.SEASON_TYPE == "Regular Season"]
    drtg = {(t, s): g.defensiveRating.mean() for (t, s), g in tg.groupby(["TEAM_TRICODE", "SEASON"])}

    dbpm_p = {(r.PLAYER_ID, r.SEASON): r.DBPM for r in war.itertuples()}
    rim_p = {(r.PLAYER_ID, r.SEASON): r.rim_val for r in m.itertuples()}
    skill_p = {(r.PLAYER_ID, r.SEASON): r.skill for r in m.itertuples()}

    rows = []
    for (tri, s), g in m.groupby(["TEAM", "SEASON"]):
        p = PREV.get(s)
        if p is None or (tri, s) not in drtg:
            continue
        recs = [(r.MIN, dbpm_p.get((r.PLAYER_ID, p)), rim_p.get((r.PLAYER_ID, p)),
                 skill_p.get((r.PLAYER_ID, p))) for r in g.itertuples()]
        recs = [(w, d, ri, sk) for w, d, ri, sk in recs
                if d is not None and ri is not None and not np.isnan(d)]
        if len(recs) < 5:
            continue
        W = sum(w for w, *_ in recs)
        rows.append({"season": s, "drtg": drtg[(tri, s)],
                     "box": sum(w * d for w, d, _, _ in recs) / W,
                     "rim": sum(w * ri for w, _, ri, _ in recs) / W,
                     "skill": sum(w * (sk or 0) for w, _, _, sk in recs) / W})
    d = pd.DataFrame(rows)
    for c in ["drtg", "box", "rim", "skill"]:
        d[c + "_dm"] = d[c] - d.groupby("season")[c].transform("mean")
    y = d.drtg_dm.to_numpy(); seasons = d.season.to_numpy()

    print(f"Defensive bake-off — {len(d)} team-seasons (predict team DRtg from prior-yr value)\n")
    print("Leave-one-season-out CV (lower RMSE = better; DRtg: lower is better defense):")
    for name, cols in [("box DBPM only", ["box_dm"]),
                       ("rim protection only", ["rim_dm"]),
                       ("box + rim protection", ["box_dm", "rim_dm"]),
                       ("box + rim + matchup skill", ["box_dm", "rim_dm", "skill_dm"])]:
        rmse, r = loso(d, y, cols, seasons)
        print(f"  {name:<28} CV RMSE {rmse:.3f}  corr {r:+.3f}")

    print("\n  Top-8 rim protectors by value, 2024-25:")
    latest = m[m.SEASON == "2024-25"].nlargest(8, "rim_val")
    nm = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "PLAYER"])
    latest = latest.merge(nm, on=["PLAYER_ID", "SEASON"])
    for r in latest.itertuples():
        print(f"    {r.PLAYER:<24} {r.TEAM:<4} +{r.rim_val:.1f} rim shots saved/g")


if __name__ == "__main__":
    main()
