"""
How well do the player projections match what actually happened?

Everything up to here has been measured either at team level (script 125's win
probability and box totals) or as FIDELITY — whether the engine reproduces the
rate book it was handed (158). Neither answers the question that matters for a
box score a person reads: across all players and all counting stats, how close
are the numbers to what those players really did?

Chasing one player is how a model gets fitted to an anecdote. This scores every
rotation player in a held-out season on every stat the simulator produces, and
compares against two references:

  SEASON TO DATE  the player's own average over his games BEFORE this one. It is
                  what a reader would guess without a model, it is leakage-safe,
                  and beating it is the minimum bar for the simulator to be worth
                  running at all.
  PROPS ENGINE    the production points model (script 71), the incumbent, on the
                  rows where it has a projection.

Reported per stat: mean absolute error, BIAS (are we systematically high or low),
and the correlation across players — a model can have a good average error and
still fail to separate the players from each other, which is what a box score is
for. Broken out by playing time, because an error of two rebounds means something
different for a starter than for a bench player.

Minutes are PROJECTED, never read from the box score, so the whole chain is
leakage-safe in the way script 125 requires.

Usage: python scripts/161_player_accuracy.py [--games 200] [--sims 120]
"""

from __future__ import annotations

import argparse
import importlib.util
import multiprocessing as mp
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "data" / "parquet" / "player_games.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
PROPS = ROOT / "data" / "features" / "props_predictions.parquet"
SIM1 = ROOT / "data" / "features" / "sim_mode1_predictions.parquet"

# simulated field -> real box column
FIELDS = {"PTS": "points", "REB": "reboundsTotal", "AST": "assists",
          "STL": "steals", "BLK": "blocks", "TOV": "turnovers",
          "FGA": "fieldGoalsAttempted", "FTA": "freeThrowsAttempted",
          "FG3M": "threePointersMade", "PF": "foulsPersonal"}
_G = {}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _one(idx):
    G = _G
    gid = G["gids"][idx]
    g = G["gs"][G["gs"].GAME_ID == gid].iloc[0]
    rr = G["pg"][G["pg"].GAME_ID == gid]
    sim = G["sim"]
    sides, ok = {}, True
    for tag, tid in (("H", g.HOME_TEAM_ID), ("A", g.AWAY_TEAM_ID)):
        d = rr[(rr.TEAM_ID == tid) & (rr.MIN > 0)]
        pl = []
        for r in d.itertuples():
            pid = int(r.PLAYER_ID)
            m = G["pmin"].get((gid, pid))
            if m is None or not np.isfinite(m):
                m = float(sim.rates[pid].get("MPG", 0.0) or 0.0) * 0.83
            if m <= 0:
                continue
            pl.append({"pid": pid, "minutes": float(m),
                       "started": int(isinstance(r.position, str)
                                      and bool(r.position.strip()))})
        if len(pl) < 6:
            ok = False
        tot = sum(p["minutes"] for p in pl)
        if tot > 0:
            for p in pl:
                p["minutes"] *= 240.0 / tot
        sides[tag] = pl
    if not ok:
        return []
    if G["dqg"]:
        sim.defq = G["dqg"].get(gid, {})
    anc = G["anchors"].get(gid)
    _, box, _ = sim.simulate(sides["H"], sides["A"], g.HOME_TEAM_ID, g.AWAY_TEAM_ID,
                             n_sims=G["ns"],
                             seed=int(np.random.default_rng([7, idx]).integers(1 << 30)),
                             anchor=anc)
    act = {int(r.PLAYER_ID): r for r in rr.itertuples()}
    out = []
    for tag in ("H", "A"):
        mins = {p["pid"]: p["minutes"] for p in sides[tag]}
        for pid, st in box[tag].items():
            a = act.get(pid)
            if a is None:
                continue
            row = {"pid": pid, "gid": gid, "proj_min": mins.get(pid, 0.0),
                   "act_min": float(a.MIN)}
            for f, col in FIELDS.items():
                row["p_" + f] = float(st[f].mean())
                row["a_" + f] = float(getattr(a, col))
            out.append(row)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--sims", type=int, default=120)
    ap.add_argument("--season", default="2024-25")
    args = ap.parse_args()

    S = load("sim124", ROOT / "scripts" / "124_possession_sim.py")
    games = pd.read_parquet(GAMES)
    gs = games[(games.SEASON == args.season)
               & (games.SEASON_TYPE == "Regular Season")]
    gids = list(gs.GAME_ID.sample(min(args.games, len(gs)), random_state=11))
    rates, pos = S.build_rates(args.season)
    tmpl = pd.read_parquet(ROOT / "data/parquet/rotation_templates.parquet")
    aff = pd.read_parquet(ROOT / "data/parquet/assignment_affinity.parquet") \
        .set_index("dpos")[S.POSITIONS].to_numpy()
    dq = S.defensive_index(args.season)
    dqg = {}
    if dq and isinstance(next(iter(dq)), tuple):
        for (gg, pp), vv in dq.items():
            dqg.setdefault(gg, {})[pp] = vv
        dq = {}
    pace_map, lg_pace = S.team_pace(args.season)
    w3 = pd.read_parquet(ROOT / "data/parquet/player_seasons_war_v3.parquet")
    w3 = w3[w3.SEASON == args.season]
    bpm = {int(r.PLAYER_ID): float(r.BPM3) for r in w3.itertuples()}
    sim = S.Simulator(rates, pos, tmpl, aff, dq, pace_map, lg_pace, bpm,
                      S.minutes_ratio_pools(args.season),
                      S.usage_ratio_pool(args.season))
    cols = ["GAME_ID", "GAME_DATE", "TEAM_ID", "PLAYER_ID", "MIN", "position"] \
        + list(FIELDS.values())
    pg = pd.read_parquet(PG, columns=list(dict.fromkeys(
        cols + ["SEASON", "SEASON_TYPE"])))
    season_pg = pg[(pg.SEASON == args.season)
                   & (pg.SEASON_TYPE == "Regular Season")].copy()
    props = pd.read_parquet(PROPS)
    props = props[props.SEASON == args.season]
    pmin = {(r.GAME_ID, int(r.PLAYER_ID)): r.pred_min for r in props.itertuples()}
    ppts = {(r.GAME_ID, int(r.PLAYER_ID)): r.pred_points for r in props.itertuples()}
    s1 = pd.read_parquet(SIM1)[["GAME_ID", "MU_HOME", "MU_AWAY"]]
    anchors = {r.GAME_ID: (r.MU_HOME, r.MU_AWAY) for r in s1.itertuples()}

    _G.update(gs=gs, pg=season_pg[season_pg.GAME_ID.isin(gids)], sim=sim,
              gids=gids, pmin=pmin, anchors=anchors, dqg=dqg, ns=args.sims)
    w = max(1, (os.cpu_count() or 2) - 2)
    with mp.get_context("fork").Pool(w) as pool:
        rows = [x for sub in pool.map(_one, range(len(gids))) for x in sub]
    d = pd.DataFrame(rows)
    print(f"{args.season}: {len(gids)} games, {len(d):,} player-games, "
          f"{args.sims} sims each\n")

    # ---- season-to-date baseline, using only games before this one ----
    season_pg = season_pg.sort_values("GAME_DATE")
    base = {}
    run = {}
    for r in season_pg.itertuples():
        pid = int(r.PLAYER_ID)
        acc = run.setdefault(pid, {f: [0.0, 0] for f in FIELDS})
        for f, col in FIELDS.items():
            n = acc[f][1]
            base[(r.GAME_ID, pid, f)] = acc[f][0] / n if n else np.nan
            acc[f][0] += float(getattr(r, col))
            acc[f][1] = n + 1
    for f in FIELDS:
        d["b_" + f] = [base.get((g, p, f), np.nan)
                       for g, p in zip(d.gid, d.pid)]

    d = d[d.proj_min >= 10]
    print(f"  rotation players (10+ projected minutes): {len(d):,}\n")
    print(f"  {'stat':<6}{'actual':>8}{'sim':>8}{'bias':>8}{'sim MAE':>10}"
          f"{'to-date MAE':>13}{'better':>9}{'corr':>7}")
    for f in FIELDS:
        p, a, b = d["p_" + f], d["a_" + f], d["b_" + f]
        ok = b.notna()
        mae_s = float((p - a).abs().mean())
        mae_b = float((b[ok] - a[ok]).abs().mean())
        r = float(np.corrcoef(p, a)[0, 1])
        print(f"  {f:<6}{a.mean():>8.2f}{p.mean():>8.2f}{p.mean()-a.mean():>+8.2f}"
              f"{mae_s:>10.3f}{mae_b:>13.3f}{(1-mae_s/mae_b)*100:>8.1f}%{r:>7.3f}")

    print("\n  BY PLAYING TIME (points)")
    d["tier"] = pd.cut(d.proj_min, [10, 18, 26, 34, 48],
                       labels=["10-18", "18-26", "26-34", "34+"])
    print(f"  {'minutes':<9}{'n':>6}{'actual':>8}{'sim':>8}{'bias':>8}"
          f"{'sim MAE':>10}{'to-date':>10}")
    for t, s in d.groupby("tier", observed=True):
        ok = s.b_PTS.notna()
        print(f"  {str(t):<9}{len(s):>6}{s.a_PTS.mean():>8.2f}{s.p_PTS.mean():>8.2f}"
              f"{s.p_PTS.mean()-s.a_PTS.mean():>+8.2f}"
              f"{float((s.p_PTS-s.a_PTS).abs().mean()):>10.3f}"
              f"{float((s.b_PTS[ok]-s.a_PTS[ok]).abs().mean()):>10.3f}")

    e = d[[(g, p) in ppts for g, p in zip(d.gid, d.pid)]].copy()
    if len(e) > 100:
        e["props"] = [ppts[(g, p)] for g, p in zip(e.gid, e.pid)]
        print(f"\n  POINTS vs the production props model ({len(e):,} shared rows)")
        print(f"    simulator  MAE {float((e.p_PTS-e.a_PTS).abs().mean()):.3f}"
              f"   bias {float((e.p_PTS-e.a_PTS).mean()):+.3f}")
        print(f"    props (71) MAE {float((e.props-e.a_PTS).abs().mean()):.3f}"
              f"   bias {float((e.props-e.a_PTS).mean()):+.3f}")


if __name__ == "__main__":
    main()
