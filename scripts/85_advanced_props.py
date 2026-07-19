"""
Advanced props experiment — do complex context features beat the standard set?

Tests, by leakage-free walk-forward backtest on player points, whether these
non-shallow features improve on the base props model:

  - VACATED_PTS: total projected scoring of this player's rotation teammates who
    are OUT this game (usage redistribution — the WOWY insight as a feature).
  - OPP_PACE: opponent's rolling possessions/game (more possessions -> more points).
  - BLOWOUT: |predicted game margin| (garbage time cuts star minutes/production).

Fed to BOTH the minutes model and the points model, since a teammate being out
raises both minutes and usage. Reports MAE base vs. advanced + importance.

Usage: python scripts/85_advanced_props.py
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error

ROOT = Path(__file__).resolve().parents[1]
FEAT = ROOT / "data" / "parquet" / "props_features.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
LOGS = ROOT / "data" / "parquet" / "game_logs.parquet"
SIM = ROOT / "data" / "features" / "sim_mode1_predictions.parquet"

RECENCY = {1: 5.0, 2: 4.0, 3: 3.0}
K = 1000.0


def project_rate(df, metric):
    order = {s: i for i, s in enumerate(sorted(df.SEASON.unique()))}
    inv = {i: s for s, i in order.items()}
    val = {(r.PLAYER_ID, r.SEASON): getattr(r, metric) for r in df.itertuples()}
    mn = {(r.PLAYER_ID, r.SEASON): r.MIN for r in df.itertuples()}
    out = {}
    for r in df.itertuples():
        ti = order[r.SEASON]
        if ti == 0:
            continue
        prior = df[df.SEASON.map(order) < ti]
        pm = np.average(prior[metric].dropna(), weights=prior.loc[prior[metric].notna(), "MIN"])
        num = den = 0.0
        for lag, w in RECENCY.items():
            s = inv.get(ti - lag)
            v, m = val.get((r.PLAYER_ID, s)), mn.get((r.PLAYER_ID, s))
            if v is not None and m is not None and not np.isnan(v):
                num += w * m * v; den += w * m
        if den:
            out[(r.PLAYER_ID, r.SEASON)] = (num + K * pm) / (den + K)
    return out


def gbm():
    return HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05,
                                         max_leaf_nodes=31, l2_regularization=1.0, random_state=7)


def walk_forward(X, y, season):
    seasons = sorted(season.unique())
    p = np.full(len(X), np.nan)
    for T in seasons[2:]:
        tr, te = (season < T).to_numpy(), (season == T).to_numpy()
        if te.sum():
            m = gbm(); m.fit(X[tr], y[tr]); p[te] = m.predict(X[te])
    return p


def main() -> None:
    ps = pd.read_parquet(PS)
    ppg = project_rate(ps, "PTS_36")
    pmpg = project_rate(ps, "MPG")
    proj_ppg = {k: ppg[k] * pmpg.get(k, 24) / 36 for k in ppg if k in pmpg}

    # --- team rosters, participants, vacated usage ---
    pg = pd.read_parquet(PG, columns=["SEASON", "SEASON_TYPE", "GAME_ID", "TEAM_ID",
                                      "PLAYER_ID", "MIN"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)]
    gp_count = pg.groupby(["TEAM_ID", "SEASON", "PLAYER_ID"]).size()
    rotation = defaultdict(list)     # (team, season) -> [pid] with >=20 gp & a projection
    for (tid, s, pid), n in gp_count.items():
        if n >= 20 and (pid, s) in proj_ppg:
            rotation[(tid, s)].append(pid)
    participants = defaultdict(set)  # (game, team) -> set of pids
    team_of = {}
    for r in pg.itertuples():
        participants[(r.GAME_ID, r.TEAM_ID)].add(r.PLAYER_ID)
        team_of[(r.GAME_ID, r.PLAYER_ID)] = r.TEAM_ID
    vacated = {}
    for (gid, tid), present in participants.items():
        s = None
        # season from any present player's row lookup is overkill; get from games below
    # simpler: recompute vacated with season via a merge
    pgg = pg[["GAME_ID", "TEAM_ID", "SEASON"]].drop_duplicates()
    for r in pgg.itertuples():
        rot = rotation.get((r.TEAM_ID, r.SEASON), [])
        present = participants[(r.GAME_ID, r.TEAM_ID)]
        vacated[(r.GAME_ID, r.TEAM_ID)] = sum(proj_ppg[(q, r.SEASON)] for q in rot if q not in present)

    # --- opponent rolling pace + blowout ---
    lg = pd.read_parquet(LOGS, columns=["GAME_ID", "TEAM_ID", "GAME_DATE", "SEASON",
                                        "SEASON_TYPE", "FGA", "FTA", "OREB", "TOV"])
    lg = lg[lg.SEASON_TYPE == "Regular Season"].copy()
    lg["poss"] = lg.FGA + 0.44 * lg.FTA - lg.OREB + lg.TOV
    lg = lg.sort_values("GAME_DATE")
    pace_hist = defaultdict(lambda: deque(maxlen=10)); prev_s = {}
    team_pace_in = {}
    for r in lg.itertuples():
        if prev_s.get(r.TEAM_ID) != r.SEASON:
            pace_hist[r.TEAM_ID].clear(); prev_s[r.TEAM_ID] = r.SEASON
        h = pace_hist[r.TEAM_ID]
        team_pace_in[(r.GAME_ID, r.TEAM_ID)] = np.mean(h) if h else 99.0
        h.append(r.poss)
    games = pd.read_parquet(GAMES)[["GAME_ID", "HOME_TEAM_ID", "AWAY_TEAM_ID"]]
    opp = {}
    for r in games.itertuples():
        opp[(r.GAME_ID, r.HOME_TEAM_ID)] = r.AWAY_TEAM_ID
        opp[(r.GAME_ID, r.AWAY_TEAM_ID)] = r.HOME_TEAM_ID
    sim = pd.read_parquet(SIM)
    blowout = {r.GAME_ID: abs(r.MU_HOME - r.MU_AWAY) for r in sim.itertuples()}

    # --- assemble ---
    df = pd.read_parquet(FEAT)
    df["TEAM_ID"] = [team_of.get((g, p)) for g, p in zip(df.GAME_ID, df.PLAYER_ID)]
    df = df.dropna(subset=["TEAM_ID"])
    df["vacated"] = [vacated.get((g, int(t)), 0.0) for g, t in zip(df.GAME_ID, df.TEAM_ID)]
    df["opp_pace"] = [team_pace_in.get((g, opp.get((g, int(t))))) or 99.0
                      for g, t in zip(df.GAME_ID, df.TEAM_ID)]
    df["blowout"] = [blowout.get(g, 8.0) for g in df.GAME_ID]
    season = df.SEASON
    mask = (season.map({s: i for i, s in enumerate(sorted(season.unique()))}) >= 2).to_numpy()
    y = df.points.to_numpy()

    MBASE = ["proj_mpg", "recent_min3", "recent_min5", "recent_min10", "started_last", "rest"]
    PBASE = ["proj_pts36", "recent_pts5", "recent_pts10", "recent_p36", "opp_def", "home", "rest"]
    ADV = ["vacated", "opp_pace", "blowout"]

    print(f"Advanced props experiment — {mask.sum():,} test player-games\n")
    for label, madd, padd in [("BASE (no context features)", [], []),
                              ("+ vacated + pace + blowout", ADV, ADV)]:
        pmin = walk_forward(df[MBASE + madd], df.MIN.to_numpy(), season)
        df["pred_min"] = np.where(np.isnan(pmin), df.proj_mpg, pmin)
        pts = walk_forward(df[["pred_min"] + PBASE + padd], y, season)
        print(f"  {label:<28} points MAE {mean_absolute_error(y[mask], pts[mask]):.4f}")

    # importance of the advanced features in the points model
    seasons = sorted(season.unique()); cut = seasons[-2]
    tr, te = (season < cut).to_numpy(), (season >= cut).to_numpy()
    cols = ["pred_min"] + PBASE + ADV
    m = gbm(); m.fit(df[cols][tr], y[tr])
    r = permutation_importance(m, df[cols][te], y[te], scoring="neg_mean_absolute_error",
                               n_repeats=5, random_state=7)
    print("\nImportance of context features (points MAE increase when shuffled):")
    for name, val in sorted(zip(cols, r.importances_mean), key=lambda t: -t[1]):
        if name in ADV:
            print(f"  {name:<12} {val:+.4f}")


if __name__ == "__main__":
    main()
