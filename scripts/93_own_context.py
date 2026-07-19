"""
Own-team context, round 3 — the player's OWN availability & load.

`vacated_*` covers teammates' availability. This round covers the player himself:

  own_missed3  = # of the team's last 3 games the player did NOT play
                 (just back from injury/rest -> minutes cap or ramp-up; recent_min
                  can't see this because it only averages games he DID play)
  own_missed10 = same over last 10 (durability / in-and-out availability)
  load3        = player's minutes in the last 3 calendar days (fatigue -> rest risk)
  blow_bench   = predicted blowout margin, but only for deep-bench players
                 (garbage time ADDS their minutes — the opposite sign to starters,
                  which is why plain `blowout` was dead)

Tested on top of the production minutes set. Winners get wired into scripts 70/71.
Usage: python scripts/93_own_context.py
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error

ROOT = Path(__file__).resolve().parents[1]
FEAT = ROOT / "data" / "parquet" / "props_features.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
SIM = ROOT / "data" / "features" / "sim_mode1_predictions.parquet"


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
    games = pd.read_parquet(GAMES)[["GAME_ID", "SEASON", "SEASON_TYPE", "GAME_DATE",
                                    "HOME_TEAM_ID", "AWAY_TEAM_ID"]]
    games = games[games.SEASON_TYPE == "Regular Season"]
    gdate = {r.GAME_ID: r.GAME_DATE for r in games.itertuples()}
    # team schedule (incl. games the player missed): (team, season) -> [game_id by date]
    sched = defaultdict(list)
    for r in games.sort_values("GAME_DATE").itertuples():
        sched[(r.HOME_TEAM_ID, r.SEASON)].append(r.GAME_ID)
        sched[(r.AWAY_TEAM_ID, r.SEASON)].append(r.GAME_ID)
    sched_pos = {k: {g: i for i, g in enumerate(v)} for k, v in sched.items()}

    pg = pd.read_parquet(PG, columns=["SEASON", "SEASON_TYPE", "GAME_ID", "GAME_DATE",
                                      "TEAM_ID", "PLAYER_ID", "MIN"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)]
    played = defaultdict(set)          # (team, season, player) -> game_ids played
    team_of = {}
    pdate_min = defaultdict(list)      # player -> [(date, min)]
    for r in pg.itertuples():
        played[(r.TEAM_ID, r.SEASON, r.PLAYER_ID)].add(r.GAME_ID)
        team_of[(r.GAME_ID, r.PLAYER_ID)] = r.TEAM_ID
        pdate_min[r.PLAYER_ID].append((r.GAME_DATE, r.MIN))
    for pid in pdate_min:
        pdate_min[pid].sort()

    sim = pd.read_parquet(SIM)
    blowout = {r.GAME_ID: abs(r.MU_HOME - r.MU_AWAY) for r in sim.itertuples()}

    df = pd.read_parquet(FEAT)
    df["TEAM_ID"] = [team_of.get((g, p)) for g, p in zip(df.GAME_ID, df.PLAYER_ID)]
    df = df.dropna(subset=["TEAM_ID"]).copy()

    def missed(gid, tid, s, pid, k):
        pos = sched_pos.get((int(tid), s), {}).get(gid)
        if pos is None or pos == 0:
            return 0
        prior = sched[(int(tid), s)][max(0, pos - k):pos]
        pl = played[(int(tid), s, pid)]
        return sum(1 for g in prior if g not in pl)

    def load3(gid, pid):
        d = gdate.get(gid)
        if d is None:
            return 0.0
        return sum(m for dt, m in pdate_min.get(pid, []) if 0 < (d - dt).days <= 3)

    df["own_missed3"] = [missed(g, t, s, p, 3) for g, t, s, p in
                         zip(df.GAME_ID, df.TEAM_ID, df.SEASON, df.PLAYER_ID)]
    df["own_missed10"] = [missed(g, t, s, p, 10) for g, t, s, p in
                          zip(df.GAME_ID, df.TEAM_ID, df.SEASON, df.PLAYER_ID)]
    df["load3"] = [load3(g, p) for g, p in zip(df.GAME_ID, df.PLAYER_ID)]
    bl = np.array([blowout.get(g, 8.0) for g in df.GAME_ID])
    df["blow_bench"] = bl * (df.proj_mpg < 18).to_numpy()

    season = df.SEASON
    y_min = df.MIN.to_numpy(); y_pts = df.points.to_numpy()
    mask = season.isin(sorted(season.unique())[2:]).to_numpy()

    MPROD = ["proj_mpg", "recent_min3", "recent_min5", "recent_min10", "started_last",
             "min_std10", "rest", "vacated_min", "vacated_pos", "vacated_delta"]
    PBASE = ["proj_pts36", "recent_pts5", "recent_pts10", "recent_p36", "opp_def", "home", "rest"]

    print(f"Own-context round 3 — {mask.sum():,} test player-games\n")
    print(f"{'minutes-model addition':<38}{'minutes MAE':>13}{'points MAE':>13}")
    for label, add in [("PRODUCTION", []),
                       ("+ own_missed3", ["own_missed3"]),
                       ("+ own_missed3 + own_missed10", ["own_missed3", "own_missed10"]),
                       ("+ load3", ["load3"]),
                       ("+ blow_bench", ["blow_bench"]),
                       ("+ ALL new", ["own_missed3", "own_missed10", "load3", "blow_bench"])]:
        pm = walk_forward(df[MPROD + add], y_min, season)
        df["pred_min"] = np.where(np.isnan(pm), df.proj_mpg, pm)
        pmae = mean_absolute_error(y_min[mask], df.pred_min.to_numpy()[mask])
        pts = walk_forward(df[["pred_min"] + PBASE], y_pts, season)
        m2 = mask & ~np.isnan(pts)
        print(f"{label:<38}{pmae:>13.4f}{mean_absolute_error(y_pts[m2], pts[m2]):>13.4f}")

    seasons = sorted(season.unique()); cut = seasons[-1]
    tr, te = (season < cut).to_numpy(), (season == cut).to_numpy()
    cols = MPROD + ["own_missed3", "own_missed10", "load3", "blow_bench"]
    mdl = gbm(); mdl.fit(df[cols][tr], y_min[tr])
    r = permutation_importance(mdl, df[cols][te], y_min[te], scoring="neg_mean_absolute_error",
                               n_repeats=5, random_state=7)
    print("\nMinutes importance (new features flagged):")
    new = {"own_missed3", "own_missed10", "load3", "blow_bench"}
    for name, val in sorted(zip(cols, r.importances_mean), key=lambda t: -t[1]):
        print(f"  {name:<14} {val:+.4f}{'  <-- new' if name in new else ''}")


if __name__ == "__main__":
    main()
