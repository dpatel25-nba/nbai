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

    # current rosters, so the exported boxes reflect this season's teams rather
    # than whoever last played for them
    rseason = ""
    RF = ROOT / "data" / "parquet" / "team_rosters.parquet"
    if RF.exists():
        _rs = pd.read_parquet(RF)
        rseason = sorted(_rs.SEASON.unique())[-1]
        _a = _rs[(_rs.SEASON == rseason) & _rs.DRAFT_SLOT.notna()]
        rates.slot_of.update({int(r.PLAYER_ID): int(r.DRAFT_SLOT)
                              for r in _a.itertuples()
                              if int(r.PLAYER_ID) not in rates})
        for r in _rs[_rs.SEASON == rseason].itertuples():
            full.setdefault(int(r.PLAYER_ID), r.PLAYER)
        print(f"roster season {rseason}: {len(_rs[_rs.SEASON == rseason])} players")
    ref_side = (MU.current_roster(ref, rates, rseason) if rseason else None) \
        or MU.roster(season, ref)[0]
    tot = sum(p["minutes"] for p in ref_side) or 1.0
    for p in ref_side:
        p["minutes"] *= 240.0 / tot

    teams = {}
    for abbr, tid in sorted(code.items()):
        sides = MU.build_sides(season, tid, ref, set(),
                               rates=rates, rseason=rseason)
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
    sides = MU.build_sides(season, hid, aid, set(),
                           rates=rates, rseason=rseason)
    surname = {int(k): str(v).split()[-1] for k, v in full.items()}
    game = M.PbpGame(S, sim, sides, hid, aid, None, np.random.default_rng(5),
                     season=season,
                     anchor=(mu0 + off[hid] - dfn[aid] + hca,
                             mu0 + off[aid] - dfn[hid]),
                     anchor_ref=MU.prior_roster(season, hid, aid)
                     if rseason else None)
    ev = game.run(surname)
    pbp = [{"c": e["clock"], "t": e["team"], "x": e["text"],
            "a": int(e["A"]), "h": int(e["H"])} for e in ev]

    # ---- everything a browser needs to run the possession loop itself ----
    # The engine is Python reading parquet, so it cannot run in a page, and
    # precomputing 435 matchups x 1000 simulations is not feasible either. What
    # IS portable is the loop: per-player rates, a rotation, and the league
    # constants. The JS port is validated against this engine in 157.
    ENG_COLS = ["FG2A_36", "FG3A_36", "FTA_36", "TOV_36", "OREB_36", "DREB_36",
                "AST_36", "PF_36", "STL_36", "BLK_36", "FG2_PCT", "FG3_PCT",
                "FT_PCT", "MPG"]
    engine = {"players": {}, "teams": {}}
    for abbr, tid in sorted(code.items()):
        sides = MU.build_sides(season, tid, ref, set(), rates=rates, rseason=rseason)
        roster_out = []
        for pl in sides["H"]:
            pid = int(pl["pid"])
            r = rates[pid]
            engine["players"][str(pid)] = {
                "n": full.get(pid, str(pid)),
                **{c: round(float(r.get(c, 0.0) or 0.0), 4) for c in ENG_COLS}}
            roster_out.append({"id": pid, "min": round(float(pl["minutes"]), 2),
                               "st": int(pl.get("started", 0))})
        prior = MU.prior_roster(season, tid, ref)["H"]
        engine["teams"][abbr] = {
            "id": tid, "pace": round(float(pace_map.get(tid, lg_pace)), 2),
            "roster": roster_out,
            # BPM of the roster the RATING was fitted on, so the browser can
            # apply the same roster-delta correction the engine does
            "base_bpm": round(float(sim.team_bpm(prior)), 3),
            "bpm": round(float(sim.team_bpm(sides["H"])), 3)}
    engine["lg"] = {k: (round(v, 5) if isinstance(v, float) else v)
                    for k, v in S.LG.items()}
    engine["const"] = {k: getattr(S, k) for k in
                       ("N_SLOTS", "SLOT_SEC", "NONSHOOT_FOUL", "PENALTY_LIMIT",
                        "PEN_FT_TRIM", "FOUL_OUT", "LINEUP_HOLD", "LINEUP_DRIFT",
                        "MEAN_REVERT", "MEAN_REVERT_TOT", "PACE_SD",
                        "SHOOT_SD_SHARED", "SHOOT_SD_TEAM", "GARBAGE_MARGIN")}
    engine["lg_pace"] = round(float(lg_pace), 2)
    # League reference sums the engine normalises rebound and assist odds by.
    # The port omitted them, so off_s/def_s collapsed from a ratio-about-one to
    # ~0.5 and offensive rebounds ran at 14% instead of 25%.
    engine["ref"] = {k: round(float(v), 4) for k, v in sim._ref.items()}
    # Allocation-share factors (script 159). Without them the browser carries the
    # 11-14% compression the engine no longer has, and the two implementations
    # disagree by more than the port's measured tolerance.
    engine["cal"] = {"__ref": {k: round(float(v), 4)
                               for k, v in sim._ref.items()}}
    for (pid, stat), v in sim.share_cal.items():
        if str(pid) in engine["players"]:
            engine["cal"].setdefault(str(pid), {})[stat] = round(float(v), 4)

    payload = {"engine": engine,
               "season": season, "mu0": round(mu0, 3), "hca": round(hca, 3),
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
