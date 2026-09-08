"""
Does the browser port agree with the Python engine?

`web/sim.js` re-implements the possession loop so the site can run simulations
on demand — the real engine reads parquet and cannot run in a page. A second
implementation of the same model is a liability unless it is checked against the
first, so this runs both on identical matchups and compares what a user actually
reads: team scores and per-player stat lines.

It is NOT a bit-for-bit test. The port deliberately omits refinements whose data
is too large to ship or which move totals by well under a point — shot zones,
defender matchup affinity, height and weight effects, cold start, transition and
endgame shot selection. So the question is whether the omissions are small, and
the answer has to be a number rather than an assurance.

Run with macOS's JavaScriptCore, which is present on any Mac:
    /System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc

Usage: python scripts/157_validate_js.py [--sims 200]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
JSC = ("/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/"
       "Helpers/jsc")
WEB_SIM = ROOT / "data" / "features" / "web_sim.json"
PAIRS = [("OKC", "DEN"), ("BOS", "NYK"), ("MIA", "UTA"), ("WAS", "LAL")]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run_js(payload: dict, home: str, away: str, sims: int) -> dict:
    """Drive web/sim.js under jsc and return aggregated results."""
    js = (ROOT / "web" / "sim.js").read_text().replace("})(window);", "})(this);")
    driver = """
    var P = DATA, E = P.engine, C = E.const, LG = E.lg;
    function ratesOf(id) { return E.players[String(id)]; }
    function side(t) {
      return E.teams[t].roster.map(function (x) {
        var p = E.players[String(x.id)];
        return {id: x.id, n: p.n, min: x.min};
      });
    }
    var H = side(HOME), A = side(AWAY);
    var pace = (E.teams[HOME].pace + E.teams[AWAY].pace) / 2;
    var mu = {H: MUH, A: MUA};
    var scale = {H: SCH, A: SCA, mu: mu};
    var tot = {H: 0, A: 0}, acc = {}, n = SIMS;
    for (var s = 0; s < n; s++) {
      var g = this.NBAI_SIM.playGame(H, A, ratesOf, C, LG, pace, scale, s + 1, false);
      tot.H += g.score.H; tot.A += g.score.A;
      for (var t in g.box) for (var pid in g.box[t]) {
        var k = t + "|" + pid;
        if (!acc[k]) { acc[k] = {}; }
        for (var f in g.box[t][pid]) acc[k][f] = (acc[k][f] || 0) + g.box[t][pid][f];
      }
    }
    var out = {H: tot.H / n, A: tot.A / n, players: {}};
    for (var k in acc) { out.players[k] = {}; for (var f in acc[k]) out.players[k][f] = acc[k][f] / n; }
    print(JSON.stringify(out));       // jsc does not echo the last expression
    """
    return js, driver


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=200)
    args = ap.parse_args()
    if not Path(JSC).exists():
        raise SystemExit("JavaScriptCore not found; cannot validate the port")
    payload = json.loads(WEB_SIM.read_text())

    S = load("sim124", ROOT / "scripts" / "124_possession_sim.py")
    MU = load("mu154", ROOT / "scripts" / "154_matchup.py")
    season = payload["season"]
    rates, pos = S.build_rates(season)
    rs = pd.read_parquet(ROOT / "data/parquet/team_rosters.parquet")
    rseason = sorted(rs.SEASON.unique())[-1]
    a = rs[(rs.SEASON == rseason) & rs.DRAFT_SLOT.notna()]
    rates.slot_of.update({int(r.PLAYER_ID): int(r.DRAFT_SLOT) for r in a.itertuples()
                          if int(r.PLAYER_ID) not in rates})
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

    g = pd.read_parquet(ROOT / "data/parquet/games.parquet")
    g = g[g.SEASON == season]
    code = {}
    for r in g.itertuples():
        code[r.HOME_TEAM] = int(r.HOME_TEAM_ID)
        code[r.AWAY_TEAM] = int(r.AWAY_TEAM_ID)
    off, dfn, mu0, hca, _ = MU.team_ratings(season)

    print(f"  {'matchup':<12}{'py home':>9}{'js home':>9}{'py away':>9}"
          f"{'js away':>9}{'|diff|':>8}")
    dif, ptsdif = [], []
    for h, aw in PAIRS:
        hid, aid = code[h], code[aw]
        sides = MU.build_sides(season, hid, aid, set(), rates=rates, rseason=rseason)
        aref = MU.prior_roster(season, hid, aid)
        mh = mu0 + off[hid] - dfn[aid] + hca
        ma = mu0 + off[aid] - dfn[hid]
        res, box, _ = sim.simulate(sides["H"], sides["A"], hid, aid,
                                   n_sims=args.sims, seed=3,
                                   anchor=(mh, ma), anchor_ref=aref)
        py = {"H": float(res["H"].mean()), "A": float(res["A"].mean())}
        pyp = {}
        for t in ("H", "A"):
            for pid, st in box[t].items():
                pyp[f"{t}|{pid}"] = {k: float(v.mean()) for k, v in st.items()}

        # the browser solves the same anchor; hand it the resulting scale
        js_src, drv = run_js(payload, h, aw, args.sims)
        sim2 = S.Simulator(rates, pos, tmpl, aff, dq, pace_map, lg_pace, bpm,
                           S.minutes_ratio_pools(season), S.usage_ratio_pool(season))
        sim2.simulate(sides["H"], sides["A"], hid, aid, n_sims=1, seed=1,
                      anchor=(mh, ma), anchor_ref=aref)
        sch, sca = sim2.team_scale["H"], sim2.team_scale["A"]
        drv = (drv.replace("HOME", json.dumps(h)).replace("AWAY", json.dumps(aw))
                  .replace("MUH", repr(mh)).replace("MUA", repr(ma))
                  .replace("SCH", repr(sch)).replace("SCA", repr(sca))
                  .replace("SIMS", str(args.sims)))
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write("var DATA = " + json.dumps(payload) + ";\n")
            f.write(js_src + "\n")
            f.write(drv)
            tmp = f.name
        out = subprocess.run([JSC, tmp], capture_output=True, text=True, timeout=600)
        if out.returncode != 0:
            raise SystemExit(f"jsc failed: {out.stderr[:400]}")
        js = json.loads(out.stdout.strip().splitlines()[-1])
        dif.append(abs(py["H"] - js["H"]))
        dif.append(abs(py["A"] - js["A"]))
        print(f"  {h+' v '+aw:<12}{py['H']:>9.1f}{js['H']:>9.1f}"
              f"{py['A']:>9.1f}{js['A']:>9.1f}"
              f"{max(abs(py['H']-js['H']), abs(py['A']-js['A'])):>8.1f}")
        for k, v in js["players"].items():
            if k in pyp and pyp[k]["PTS"] > 4:
                ptsdif.append(abs(pyp[k]["PTS"] - v["PTS"]))

    print(f"\n  team score   mean |diff| {np.mean(dif):.2f} pts   max {np.max(dif):.2f}")
    if ptsdif:
        print(f"  player points mean |diff| {np.mean(ptsdif):.2f} pts   "
              f"max {np.max(ptsdif):.2f}   (n={len(ptsdif)} rotation players)")
    print("\n  The port omits shot zones, defender affinity, height/weight, cold")
    print("  start and endgame shot selection. These figures are the cost of that.")


if __name__ == "__main__":
    main()
