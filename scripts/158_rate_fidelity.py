"""
Does the engine give a player the rate it was told to?

Every counting stat is allocated by drawing one of the five on court in
proportion to their own per-36 rates: rebounds, assists, steals, blocks, and the
possession itself. That is only correct if a player's share WITHIN a lineup
equals his rate ACROSS all lineups, and it does not. A rate is earned in the mix
of team-mates a player actually plays with; drop him into one specific five and
proportional weighting redistributes him toward the middle. The elite rebounder
is the visible case — Jokic projects at 10.5 against a real 12.9 — but the
mechanism is generic and applies to every allocated stat.

This measures the size of it. For each matchup the engine is run, each player's
simulated per-36 is compared against the rate the book handed it, and the ratio
is bucketed by how extreme the input was. A ratio of 1.0 means the engine is
faithful; a ratio that FALLS as the input rises is the compression.

It is deliberately a fidelity test and not an accuracy test. Whether the rate
book is right about a player is script 125's question. This asks the narrower
one that has to be answered first: whatever the book says, does the simulation
reproduce it? An engine that cannot hold its own inputs cannot be tuned by
improving them.

Usage: python scripts/158_rate_fidelity.py [--sims 200] [--matchups 6]
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

# simulated box field  ->  the rate-book column that is supposed to produce it
# Only fields the engine actually books. It tracks REB as a single counter and
# has no FG3A, so asking for OREB/DREB/FG3A reported a fidelity of 0.000 — a
# property of the diagnostic, not of the model.
PAIRS = [("REB", ("OREB_36", "DREB_36")), ("AST", ("AST_36",)),
         ("STL", ("STL_36",)), ("BLK", ("BLK_36",)), ("TOV", ("TOV_36",)),
         ("PF", ("PF_36",)), ("FGA", ("FG2A_36", "FG3A_36")),
         ("FTA", ("FTA_36",))]
MATCHUPS = [("DEN", "OKC"), ("BOS", "NYK"), ("MIN", "LAL"), ("PHI", "CLE"),
            ("MEM", "SAC"), ("MIA", "ORL"), ("HOU", "GSW"), ("MIL", "IND")]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=200)
    ap.add_argument("--matchups", type=int, default=6)
    ap.add_argument("--min-minutes", type=float, default=12.0)
    args = ap.parse_args()

    S = load("sim124", ROOT / "scripts" / "124_possession_sim.py")
    MU = load("mu154", ROOT / "scripts" / "154_matchup.py")
    g = pd.read_parquet(ROOT / "data/parquet/games.parquet")
    season = sorted(g.SEASON.unique())[-1]
    g = g[g.SEASON == season]
    code = {}
    for r in g.itertuples():
        code[r.HOME_TEAM] = int(r.HOME_TEAM_ID)
        code[r.AWAY_TEAM] = int(r.AWAY_TEAM_ID)

    rates, pos = S.build_rates(season)
    rs = pd.read_parquet(ROOT / "data/parquet/team_rosters.parquet")
    rseason = sorted(rs.SEASON.unique())[-1]
    a = rs[(rs.SEASON == rseason) & rs.DRAFT_SLOT.notna()]
    rates.slot_of.update({int(r.PLAYER_ID): int(r.DRAFT_SLOT)
                          for r in a.itertuples() if int(r.PLAYER_ID) not in rates})
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
    off, dfn, mu0, hca, _ = MU.team_ratings(season)

    rows = []
    for h, aw in MATCHUPS[:args.matchups]:
        if h not in code or aw not in code:
            continue
        hid, aid = code[h], code[aw]
        sides = MU.build_sides(season, hid, aid, set(), rates=rates, rseason=rseason)
        aref = MU.prior_roster(season, hid, aid)
        _, box, _ = sim.simulate(sides["H"], sides["A"], hid, aid,
                                 n_sims=args.sims, seed=5,
                                 anchor=(mu0 + off[hid] - dfn[aid] + hca,
                                         mu0 + off[aid] - dfn[hid]),
                                 anchor_ref=aref)
        for tag in ("H", "A"):
            mins = {p["pid"]: p["minutes"] for p in sides[tag]}
            for pid, st in box[tag].items():
                m = mins.get(pid, 0.0)
                if m < args.min_minutes:
                    continue
                for field, cols in PAIRS:
                    inp = sum(float(rates[pid].get(c, 0.0) or 0.0) for c in cols)
                    if inp <= 0.05:
                        continue
                    rows.append({"stat": field, "input": inp, "min": m,
                                 "team": f"{h}{aw}{tag}",
                                 "raw": float(st[field].mean()),
                                 "target_raw": inp * m / 36.0,
                                 "sim": float(st[field].mean()) / m * 36.0})
    d = pd.DataFrame(rows)
    d["ratio"] = d.sim / d.input
    print(f"{season}: {args.matchups} matchups x {args.sims} sims, "
          f"{len(d):,} player-stat observations\n")

    print("  OVERALL FIDELITY BY STAT")
    print(f"  {'stat':<6}{'n':>6}{'mean input':>12}{'mean sim':>10}{'ratio':>8}"
          f"{'top-quintile ratio':>21}")
    for field, _ in PAIRS:
        s = d[d.stat == field]
        if len(s) < 20:
            continue
        cut = s.input.quantile(0.80)
        top = s[s.input >= cut]
        print(f"  {field:<6}{len(s):>6}{s.input.mean():>12.2f}{s.sim.mean():>10.2f}"
              f"{s.sim.sum()/s.input.sum():>8.3f}"
              f"{top.sim.sum()/top.input.sum():>21.3f}")

    print("\n  COMPRESSION: ratio by how extreme the input is")
    print("  (a falling ratio means the engine pulls players toward the middle)")
    print(f"  {'stat':<6}" + "".join(f"{q:>10}" for q in
                                     ["q1 (low)", "q2", "q3", "q4", "q5 (high)"]))
    for field, _ in PAIRS:
        s = d[d.stat == field].copy()
        if len(s) < 40:
            continue
        s["q"] = pd.qcut(s.input, 5, labels=False, duplicates="drop")
        cells = []
        for q in range(5):
            t = s[s.q == q]
            cells.append(f"{t.sim.sum()/t.input.sum():>10.3f}" if len(t) else
                         f"{'—':>10}")
        print(f"  {field:<6}" + "".join(cells))

    # Split the error in two. A weight can only move a player's SHARE of his
    # team's total; if the total itself is off, no reallocation reaches the
    # per-player rate. Reporting them together makes a correctly-split stat look
    # broken, which is what the first pass of this diagnostic did.
    print("\n  SPLIT vs TOTAL: where the remaining error lives")
    print(f"  {'stat':<6}{'team total':>12}{'share q1':>10}{'share q5':>10}"
          f"{'   verdict':>12}")
    for field, _ in PAIRS:
        s2 = d[d.stat == field].copy()
        if len(s2) < 40:
            continue
        tot = s2.groupby("team").agg(t=("target_raw", "sum"), o=("raw", "sum"))
        tot_ratio = float((tot.o / tot.t).mean())
        s2 = s2.merge(tot.rename(columns={"t": "tt", "o": "to"}),
                      left_on="team", right_index=True)
        s2["sh_t"] = s2.target_raw / s2.tt
        s2["sh_o"] = s2.raw / s2.to
        s2["q"] = pd.qcut(s2.input, 5, labels=False, duplicates="drop")
        q1 = s2[s2.q == 0]
        q5 = s2[s2.q == s2.q.max()]
        r1 = float(q1.sh_o.sum() / q1.sh_t.sum())
        r5 = float(q5.sh_o.sum() / q5.sh_t.sum())
        v = "split ok" if abs(r5 - 1) < 0.05 else "split off"
        print(f"  {field:<6}{tot_ratio:>12.3f}{r1:>10.3f}{r5:>10.3f}{v:>12}")

    worst = []
    for field, _ in PAIRS:
        s = d[d.stat == field]
        if len(s) < 40:
            continue
        cut = s.input.quantile(0.80)
        top = s[s.input >= cut]
        worst.append((field, top.sim.sum() / top.input.sum(), len(top)))
    worst.sort(key=lambda x: abs(x[1] - 1), reverse=True)
    print("\n  Worst top-quintile fidelity:")
    for f, r, n in worst[:5]:
        print(f"    {f:<6} {r:.3f}  ({'under' if r < 1 else 'over'}-produces "
              f"the book by {abs(1-r)*100:.1f}%, n={n})")


if __name__ == "__main__":
    main()
