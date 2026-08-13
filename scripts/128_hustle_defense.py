"""
WAR bake-off, defensive side: do HUSTLE stats add over box DBPM + rim protection?

Script 110 showed tracking rim protection beats box DBPM for predicting team
defense, and script 89 showed matchup skill adds nothing. The newly parsed hustle
data (script 127) is the remaining untested defensive source, and it is the only
one that observes defensive ACTIVITY directly rather than inferring it:

    DEFLECTIONS        possessions disrupted without a steal being credited
    CONTESTED_SHOTS    contests at the rim and on the perimeter, split 2PT/3PT
    CHARGES_DRAWN      a forced turnover the box score files as a foul
    DEF_BOXOUTS        rebounding work that never appears as a rebound

Same honest test as 110: a defensive metric earns its place only if it is
PORTABLE — take players' PRIOR-season values, weight by their minutes on THIS
season's roster, and predict the team's actual defensive rating. Roster churn
makes that a real out-of-sample test; a metric that merely refits the same season
proves nothing.

Evaluated by leave-one-season-out CV, season-demeaned so league-wide defensive
drift cannot flatter any model. Lower DRtg = better defense.

Usage: python scripts/128_hustle_defense.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HUS = ROOT / "data" / "parquet" / "player_hustle.parquet"
TRK = ROOT / "data" / "parquet" / "player_tracking.parquet"
WAR = ROOT / "data" / "parquet" / "player_seasons_war.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
TG = ROOT / "data" / "parquet" / "team_games.parquet"

ORDER = [f"{y}-{str(y + 1)[2:]}" for y in range(2013, 2026)]
PREV = {s: ORDER[i - 1] for i, s in enumerate(ORDER) if i}
MIN_MIN = 500


def loso(d, y, cols, seasons):
    pred = np.full(len(d), np.nan)
    for s in np.unique(seasons):
        tr, te = seasons != s, seasons == s
        A = np.column_stack([np.ones(tr.sum())] + [d[c].to_numpy()[tr] for c in cols])
        b, *_ = np.linalg.lstsq(A, y[tr], rcond=None)
        pred[te] = np.column_stack([np.ones(te.sum())]
                                   + [d[c].to_numpy()[te] for c in cols]) @ b
    return np.sqrt(np.mean((y - pred) ** 2)), float(np.corrcoef(pred, y)[0, 1])


def main() -> None:
    hus = pd.read_parquet(HUS)
    hus = hus[hus.SEASON_TYPE == "Regular Season"].copy()
    hus["mins"] = pd.to_numeric(
        hus.MINUTES.astype(str).str.split(":").str[0], errors="coerce").fillna(0)
    g = hus.groupby(["PLAYER_ID", "SEASON"]).agg(
        gp=("GAME_ID", "size"), mins=("mins", "sum"),
        defl=("DEFLECTIONS", "sum"), cont=("CONTESTED_SHOTS", "sum"),
        cont3=("CONTESTED_SHOTS_3PT", "sum"), chg=("CHARGES_DRAWN", "sum"),
        dbox=("DEF_BOXOUTS", "sum")).reset_index()
    g = g[g.mins >= MIN_MIN].copy()
    for c in ("defl", "cont", "cont3", "chg", "dbox"):
        g[c + "36"] = g[c] / g.mins * 36
    g["PLAYER_ID"] = g.PLAYER_ID.astype("int64")

    # rim protection, the incumbent from script 110
    trk = pd.read_parquet(TRK)
    trk = trk[trk.MIN.notna() & (trk.MIN > 0)].copy()
    lgr = (trk.dropna(subset=["DEF_RIM_FG_PCT", "DEF_RIM_FGA"]).groupby("SEASON")
              .apply(lambda d: np.average(d.DEF_RIM_FG_PCT, weights=d.DEF_RIM_FGA + 1e-9),
                     include_groups=False).rename("lgr").reset_index())
    trk = trk.merge(lgr, on="SEASON", how="left")
    trk["rim"] = trk.DEF_RIM_FGA.fillna(0) * (trk.lgr - trk.DEF_RIM_FG_PCT.fillna(trk.lgr))

    war = pd.read_parquet(WAR, columns=["PLAYER_ID", "SEASON", "DBPM"])
    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "PLAYER", "TEAM", "MIN"])
    m = (ps.merge(war, on=["PLAYER_ID", "SEASON"], how="left")
           .merge(trk[["PLAYER_ID", "SEASON", "rim"]], on=["PLAYER_ID", "SEASON"], how="left")
           .merge(g[["PLAYER_ID", "SEASON", "defl36", "cont36", "cont336",
                     "chg36", "dbox36"]], on=["PLAYER_ID", "SEASON"], how="left"))
    m = m[m.MIN >= MIN_MIN].copy()
    for c in ["rim", "defl36", "cont36", "cont336", "chg36", "dbox36"]:
        m[c] = m[c].fillna(0.0)

    tg = pd.read_parquet(TG)
    tg = tg[tg.SEASON_TYPE == "Regular Season"]
    drtg = {(t, s): d.defensiveRating.mean()
            for (t, s), d in tg.groupby(["TEAM_TRICODE", "SEASON"])}

    FEATS = ["DBPM", "rim", "defl36", "cont36", "cont336", "chg36", "dbox36"]
    look = {c: {(r.PLAYER_ID, r.SEASON): getattr(r, c) for r in m.itertuples()}
            for c in FEATS}

    rows = []
    for (tri, s), grp in m.groupby(["TEAM", "SEASON"]):
        p = PREV.get(s)
        if p is None or (tri, s) not in drtg:
            continue
        recs = [(r.MIN, {c: look[c].get((r.PLAYER_ID, p)) for c in FEATS})
                for r in grp.itertuples()]
        recs = [(w, v) for w, v in recs
                if v["DBPM"] is not None and not np.isnan(v["DBPM"])]
        if len(recs) < 5:
            continue
        W = sum(w for w, _ in recs)
        row = {"season": s, "drtg": drtg[(tri, s)]}
        for c in FEATS:
            row[c] = sum(w * (v[c] if v[c] is not None and not np.isnan(v[c]) else 0.0)
                         for w, v in recs) / W
        rows.append(row)

    d = pd.DataFrame(rows)
    # hustle coverage begins 2016-17; restrict so every model sees the same rows
    d = d[d.season >= "2017-18"].reset_index(drop=True)
    for c in ["drtg"] + FEATS:
        d[c + "_dm"] = d[c] - d.groupby("season")[c].transform("mean")
    y = d.drtg_dm.to_numpy()
    seasons = d.season.to_numpy()
    print(f"Hustle defensive bake-off — {len(d)} team-seasons "
          f"({d.season.min()}…{d.season.max()})\n")

    print("Within-season correlation with team DRtg (negative = predicts better D):")
    for c in FEATS:
        print(f"  {c:<10} r = {np.corrcoef(d[c + '_dm'], y)[0, 1]:+.3f}")

    print("\nLeave-one-season-out CV (lower RMSE = better):")
    models = [
        ("box DBPM only", ["DBPM_dm"]),
        ("box + rim (script 110)", ["DBPM_dm", "rim_dm"]),
        ("box + rim + deflections", ["DBPM_dm", "rim_dm", "defl36_dm"]),
        ("box + rim + contests", ["DBPM_dm", "rim_dm", "cont36_dm", "cont336_dm"]),
        ("box + rim + ALL hustle", ["DBPM_dm", "rim_dm", "defl36_dm", "cont36_dm",
                                    "cont336_dm", "chg36_dm", "dbox36_dm"]),
    ]
    base = None
    for name, cols in models:
        rmse, r = loso(d, y, cols, seasons)
        if base is None:
            base = rmse
        tag = "" if name.startswith("box DBPM") else f"   {(rmse/base - 1) * 100:+.1f}% vs box"
        print(f"  {name:<28} CV RMSE {rmse:.3f}  corr {r:+.3f}{tag}")

    print("\nFace check — 2024-25 deflection leaders per 36 (>=500 min):")
    q = m[m.SEASON == "2024-25"].nlargest(6, "defl36")
    for r in q.itertuples():
        print(f"  {r.PLAYER:<24} {r.TEAM:<4} {r.defl36:.2f} defl/36  "
              f"{r.cont36:.1f} contests/36")


if __name__ == "__main__":
    main()
