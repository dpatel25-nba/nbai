"""
Export the simulator's current state for the website.

The site is static: `export_web.py` writes one `web/data.js` and the page reads
it. Running the possession engine for all 435 team pairs at page-load time is
not possible, and precomputing them is not either, so this exports the pieces a
browser can recombine:

  RATINGS   the standalone team-strength fit from script 154 (offence, defence,
            intercept, home edge). Any matchup's projected score is then two
            subtractions in JavaScript, and it is the SAME rating the simulator
            anchors to — not a second model that could disagree with it.

  BASELINE  each team's projected box score, produced by actually running the
            possession engine, against a fixed league-median opponent. The page
            scales the scoring columns by the matchup's projected total.

  PBP       one full play-by-play, rendered by script 134, so the site shows
            real engine output rather than a mock-up.

THE SCALING IS AN APPROXIMATION AND THE PAGE SAYS SO. Points, shots and free
throws are scaled by the ratio of the matchup's projected team total to the
baseline total; minutes, rebounds, assists, steals, blocks and turnovers are
left alone because they track pace and role rather than efficiency. A real
per-matchup simulation would differ, mostly in the defensive matchup terms,
which the engine measured as small.

Output: data/features/web_sim.json
Usage: python scripts/155_export_sim.py [--sims 60]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GAMES = ROOT / "data" / "parquet" / "games.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
OUT = ROOT / "data" / "features" / "web_sim.json"

SCALED = ("PTS", "FGA", "FGM", "FG3A", "FG3M", "FTA", "FTM")
FLAT = ("MIN", "REB", "OREB", "DREB", "AST", "STL", "BLK", "TOV", "PF")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=60)
    ap.add_argument("--season", default="")
    args = ap.parse_args()

    S = load("sim124", ROOT / "scripts" / "124_possession_sim.py")
    MU = load("mu154", ROOT / "scripts" / "154_matchup.py")
    M = load("pbp134", ROOT / "scripts" / "134_pbp_sim.py")

    g = pd.read_parquet(GAMES)
    season = args.season or sorted(g.SEASON.unique())[-1]
    gs = g[g.SEASON == season]
    code, name_of = {}, {}
    for r in gs.itertuples():
        code[r.HOME_TEAM] = int(r.HOME_TEAM_ID)
        code[r.AWAY_TEAM] = int(r.AWAY_TEAM_ID)
    name_of = {v: k for k, v in code.items()}

    off, dfn, mu0, hca, ngames = MU.team_ratings(season)
    print(f"{season}: ratings from {ngames:,} games, home edge {hca:+.2f}")

    # league-median opponent: the team whose net rating is closest to the middle
    net = {t: off[t] - dfn[t] for t in off}
    ref = sorted(net, key=lambda t: abs(net[t] - np.median(list(net.values()))))[0]
    print(f"reference opponent: {name_of.get(ref, ref)} (median net rating)")

    rates, pos = S.build_rates(season)
    tmpl = pd.read_parquet(ROOT / "data/parquet/rotation_templates.parquet")
    aff = pd.read_parquet(ROOT / "data/parquet/assignment_affinity.parquet") \
        .set_index("dpos")[S.POSITIONS].to_numpy()
    dq = S.defensive_index(season)
    if dq and isinstance(next(iter(dq)), tuple):
        dq = {}
    pace_map, lg_pace = S.team_pace(season)
    w3 = pd.read_parquet(ROOT / "data/parquet/player_seasons_war_v3.parquet")
    w3 = w3[w3.SEASON == season]
    bpm = {int(r.PLAYER_ID): float(r.BPM3) for r in w3.itertuples()}
    sim = S.Simulator(rates, pos, tmpl, aff, dq, pace_map, lg_pace, bpm,
                      S.minutes_ratio_pools(season), S.usage_ratio_pool(season))

    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "PLAYER"])
    full = {int(r.PLAYER_ID): r.PLAYER for r in ps[ps.SEASON == season].itertuples()}

    ref_side, _ = MU.roster(season, ref)
    tot = sum(p["minutes"] for p in ref_side) or 1.0
    for p in ref_side:
        p["minutes"] *= 240.0 / tot

    teams = {}
    for abbr, tid in sorted(code.items()):
        sides = MU.build_sides(season, tid, ref, set())
        sides["A"] = ref_side
        mu_h = mu0 + off[tid] - dfn[ref]
        mu_a = mu0 + off[ref] - dfn[tid]
        res, box, _ = sim.simulate(sides["H"], sides["A"], tid, ref,
                                   n_sims=args.sims, seed=7,
                                   anchor=(mu_h, mu_a))
        players = []
        for pid, st in box["H"].items():
            mins = next((p["minutes"] for p in sides["H"] if p["pid"] == pid), 0.0)
            if mins < 2.0:
                continue
            row = {"id": int(pid), "name": full.get(int(pid), str(pid)),
                   "MIN": round(float(mins), 1)}
            for k in SCALED + FLAT[1:]:
                row[k] = round(float(st[k].mean()), 2)
            players.append(row)
        players.sort(key=lambda r: -r["MIN"])
        teams[abbr] = {"id": tid, "off": round(off[tid], 2), "def": round(dfn[tid], 2),
                       "baseline_total": round(float(res["H"].mean()), 2),
                       "players": players}
        print(f"  {abbr}  proj {mu_h:6.1f}  simulated {float(res['H'].mean()):6.1f}  "
              f"{len(players)} players", flush=True)

    # one real play-by-play so the page shows engine output, not a mock-up
    top = sorted(code, key=lambda a: -net[code[a]])[:2]
    hid, aid = code[top[0]], code[top[1]]
    sides = MU.build_sides(season, hid, aid, set())
    surname = {int(k): str(v).split()[-1] for k, v in full.items()}
    game = M.PbpGame(S, sim, sides, hid, aid, None, np.random.default_rng(5),
                     season=season)
    ev = game.run(surname)
    pbp = [{"c": e["clock"], "t": e["team"], "x": e["text"],
            "a": int(e["A"]), "h": int(e["H"])} for e in ev]

    payload = {"season": season, "mu0": round(mu0, 3), "hca": round(hca, 3),
               "ref": name_of.get(ref, str(ref)), "games_fit": int(ngames),
               "sims": int(args.sims), "teams": teams,
               "sample": {"home": top[0], "away": top[1],
                          "score": {"H": int(game.score["H"]), "A": int(game.score["A"])},
                          "events": pbp,
                          "box": {t: [{"name": surname.get(int(p), str(p)),
                                       **{k: int(v[k]) for k in
                                          ("FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA",
                                           "OREB", "DREB", "REB", "AST", "STL", "BLK",
                                           "TOV", "PF", "PTS")},
                                       "MIN": int(v["SEC"] // 60)}
                                      for p, v in sorted(game.box[t].items(),
                                                         key=lambda kv: -kv[1]["SEC"])
                                      if v["SEC"] >= 60]
                                  for t in ("H", "A")}}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload))
    kb = OUT.stat().st_size / 1024
    print(f"\nWrote {OUT}  ({kb:.0f} KB, {len(teams)} teams, {len(pbp)} pbp events)")


if __name__ == "__main__":
    main()
