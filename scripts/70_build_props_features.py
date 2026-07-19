"""
Comprehensive leakage-free feature set for the player-props engine.

One row per player-game with everything the props engine needs: projected per-36
rates (PTS/REB/AST) and MPG, minutes-model features, per-stat recent form, and
context (opponent defense, home, rest). Targets: actual MIN / points / rebounds /
assists. Built for honest pre-game prediction (projected, not actual, minutes).

Output: data/parquet/props_features.parquet
Usage: python scripts/70_build_props_features.py
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT = ROOT / "data" / "parquet" / "props_features.parquet"

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


def bucket(pos):
    """Coarse position bucket from player_seasons POS (starters-only per game)."""
    if not isinstance(pos, str):
        return "?"
    for p in ("C", "F", "G"):
        if p in pos:
            return p
    return "?"


def team_defense(games):
    off = defaultdict(float); dfn = defaultdict(float)
    league = games.HOME_PTS.head(200).mean(); prev = None; face = {}
    for g in games.itertuples():
        if prev is not None and g.SEASON != prev:
            for d in (off, dfn):
                for t in list(d): d[t] *= 0.75
        prev = g.SEASON
        h, a = g.HOME_TEAM_ID, g.AWAY_TEAM_ID
        face[(g.GAME_ID, h)] = dfn[a]; face[(g.GAME_ID, a)] = dfn[h]
        mu_h = league + off[h] + dfn[a] + 2.8; mu_a = league + off[a] + dfn[h]
        eh, ea = g.HOME_PTS - mu_h, g.AWAY_PTS - mu_a
        off[h] += 0.075 * eh; dfn[a] += 0.075 * eh
        off[a] += 0.075 * ea; dfn[h] += 0.075 * ea
        league += 0.01 * ((g.HOME_PTS + g.AWAY_PTS) / 2 - league)
    return face


def main() -> None:
    ps = pd.read_parquet(PS)
    proj = {s: project_rate(ps, f"{s}_36") for s in ("PTS", "REB", "AST")}
    proj_mpg = project_rate(ps, "MPG")
    games = pd.read_parquet(GAMES).sort_values(["GAME_DATE", "GAME_ID"])
    face = team_defense(games)

    pg = pd.read_parquet(PG, columns=["GAME_ID", "GAME_DATE", "SEASON", "SEASON_TYPE",
                                      "TEAM_ID", "HOME_AWAY", "PLAYER_ID", "MIN", "position",
                                      "points", "reboundsTotal", "assists",
                                      "fieldGoalsAttempted", "freeThrowsAttempted"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN > 0)].copy()
    pg["started"] = pg.position.fillna("").str.len().gt(0).astype(int)
    pg = pg.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)

    # --- vacated: minutes freed up by rotation teammates OUT this game (validated
    #     minutes lever, script 91). vacated_min = total absent projected minutes;
    #     vacated_pos = absent minutes at the focal player's own position. ---
    gp_count = pg.groupby(["TEAM_ID", "SEASON", "PLAYER_ID"]).size()
    rotation = defaultdict(list)
    for (tid, s, pid), n in gp_count.items():
        if n >= 20 and (pid, s) in proj_mpg:
            rotation[(tid, s)].append(pid)
    participants = defaultdict(set)
    for r in pg.itertuples():
        participants[(r.GAME_ID, r.TEAM_ID)].add(r.PLAYER_ID)
    pos_of = {(r.PLAYER_ID, r.SEASON): bucket(r.POS) for r in ps.itertuples()}
    vac_min, vac_pos = {}, {}
    for r in pg[["GAME_ID", "TEAM_ID", "SEASON"]].drop_duplicates().itertuples():
        present = participants[(r.GAME_ID, r.TEAM_ID)]
        tot = 0.0; bypos = defaultdict(float)
        for q in rotation.get((r.TEAM_ID, r.SEASON), []):
            if q not in present:
                m = proj_mpg[(q, r.SEASON)]
                tot += m; bypos[pos_of.get((q, r.SEASON), "?")] += m
        vac_min[(r.GAME_ID, r.TEAM_ID)] = tot
        vac_pos[(r.GAME_ID, r.TEAM_ID)] = dict(bypos)

    # team schedule incl. games the player missed (for own-availability features);
    # `games` is already date-sorted. played_full = every game a player actually played.
    rs = games[games.SEASON_TYPE == "Regular Season"]
    sched = defaultdict(list)
    for r in rs.itertuples():
        sched[(r.HOME_TEAM_ID, r.SEASON)].append(r.GAME_ID)
        sched[(r.AWAY_TEAM_ID, r.SEASON)].append(r.GAME_ID)
    sched_pos = {k: {g: i for i, g in enumerate(v)} for k, v in sched.items()}
    played_full = defaultdict(set)
    for r in pg.itertuples():
        played_full[(r.TEAM_ID, r.SEASON, r.PLAYER_ID)].add(r.GAME_ID)

    hist = defaultdict(lambda: deque(maxlen=10))   # (min, pts, reb, ast, started, fga, fta)
    vac_hist = defaultdict(lambda: deque(maxlen=10))  # tonight's vacated_min per past game
    date_hist = defaultdict(lambda: deque(maxlen=10))  # (date, min) for recent-load
    season_ts = defaultdict(lambda: [0.0, 0.0, 0.0])   # (pts, fga, fta) season-to-date for TS baseline
    lastdate = {}
    rows = []
    for r in pg.itertuples():
        pm = proj_mpg.get((r.PLAYER_ID, r.SEASON))
        pjs = {s: proj[s].get((r.PLAYER_ID, r.SEASON)) for s in ("PTS", "REB", "AST")}
        h = hist[r.PLAYER_ID]
        if pm is not None and all(v is not None for v in pjs.values()) and len(h) >= 3:
            M = np.array([e[0] for e in h]); P = np.array([e[1] for e in h])
            R = np.array([e[2] for e in h]); A = np.array([e[3] for e in h]); S = [e[4] for e in h]
            FGA = np.array([e[5] for e in h]); FTA = np.array([e[6] for e in h])
            per36 = lambda X: (X / M * 36)
            tsw = lambda p, fa, ft: p / (2 * (fa + 0.44 * ft)) if (fa + 0.44 * ft) > 0 else np.nan
            acc = season_ts[(r.PLAYER_ID, r.SEASON)]
            ts10 = tsw(P.sum(), FGA.sum(), FTA.sum())
            sts = tsw(acc[0], acc[1], acc[2]) if acc[1] > 0 else ts10
            rest = (r.GAME_DATE - lastdate[r.PLAYER_ID]).days if r.PLAYER_ID in lastdate else 5
            mypos = pos_of.get((r.PLAYER_ID, r.SEASON), "?")
            tonight_vac = vac_min.get((r.GAME_ID, r.TEAM_ID), 0.0)
            vh = vac_hist[r.PLAYER_ID]
            # own availability: team games missed recently + last-3-day minutes load
            pos = sched_pos.get((r.TEAM_ID, r.SEASON), {}).get(r.GAME_ID) or 0
            plset = played_full[(r.TEAM_ID, r.SEASON, r.PLAYER_ID)]
            teamsched = sched[(r.TEAM_ID, r.SEASON)]
            own_m3 = sum(1 for g in teamsched[max(0, pos - 3):pos] if g not in plset)
            own_m10 = sum(1 for g in teamsched[max(0, pos - 10):pos] if g not in plset)
            load3 = sum(m for dt, m in date_hist[r.PLAYER_ID]
                        if 0 < (r.GAME_DATE - dt).days <= 3)
            rows.append({
                "SEASON": r.SEASON, "PLAYER_ID": r.PLAYER_ID, "GAME_ID": r.GAME_ID,
                "MIN": r.MIN, "points": r.points, "reb": r.reboundsTotal, "ast": r.assists,
                "proj_mpg": pm, "proj_pts36": pjs["PTS"], "proj_reb36": pjs["REB"], "proj_ast36": pjs["AST"],
                "recent_min3": M[-3:].mean(), "recent_min5": M[-5:].mean(), "recent_min10": M.mean(),
                "started_last": S[-1], "min_std10": M.std(),
                "vacated_min": tonight_vac,
                "vacated_pos": vac_pos.get((r.GAME_ID, r.TEAM_ID), {}).get(mypos, 0.0) if mypos != "?" else 0.0,
                "vacated_delta": (np.mean(vh) - tonight_vac) if vh else 0.0,
                "own_missed3": own_m3, "own_missed10": own_m10, "load3": load3,
                "recent_fga36_5": FGA[-5:].sum() / M[-5:].sum() * 36,
                "recent_fga36_10": FGA.sum() / M.sum() * 36,
                "recent_ts5": tsw(P[-5:].sum(), FGA[-5:].sum(), FTA[-5:].sum()),
                "recent_ts10": ts10,
                "recent_ts_delta": (ts10 - sts) if not (np.isnan(ts10) or np.isnan(sts)) else 0.0,
                "recent_pts5": P[-5:].mean(), "recent_pts10": P.mean(), "recent_p36": per36(P).mean(),
                "recent_reb5": R[-5:].mean(), "recent_reb10": R.mean(), "recent_r36": per36(R).mean(),
                "recent_ast5": A[-5:].mean(), "recent_ast10": A.mean(), "recent_a36": per36(A).mean(),
                "opp_def": face.get((r.GAME_ID, r.TEAM_ID), 0.0),
                "home": int(r.HOME_AWAY == "HOME"), "rest": min(rest, 7), "b2b": int(rest == 1),
            })
        hist[r.PLAYER_ID].append((r.MIN, r.points, r.reboundsTotal, r.assists, r.started,
                                  r.fieldGoalsAttempted, r.freeThrowsAttempted))
        vac_hist[r.PLAYER_ID].append(vac_min.get((r.GAME_ID, r.TEAM_ID), 0.0))
        date_hist[r.PLAYER_ID].append((r.GAME_DATE, r.MIN))
        acc = season_ts[(r.PLAYER_ID, r.SEASON)]
        acc[0] += r.points; acc[1] += r.fieldGoalsAttempted; acc[2] += r.freeThrowsAttempted
        lastdate[r.PLAYER_ID] = r.GAME_DATE

    df = pd.DataFrame(rows)
    df.to_parquet(OUT, index=False)
    print(f"Wrote {len(df):,} player-games x {df.shape[1]} cols to {OUT}")


if __name__ == "__main__":
    main()
