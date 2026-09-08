"""
Solve the allocation weights so the engine reproduces the rates it was given.

Script 158 measured the defect: every counting stat is handed out in proportion
to the on-court players' per-36 rates, and a rate earned across a player's real
mix of team-mates does not survive being renormalised inside one specific five.
The top quintile comes back 11-14% short on every stat and the bottom quintile
up to 40% long. Jokic projecting 10.5 rebounds against a real 12.9 is the
visible case of a completely general problem.

THE FIX IS A FIXED POINT, not a model change. Let w_i be the weight a player
carries into the draw and t_i the count his rate implies over the minutes he
plays. Simulate, observe what he actually got, and scale:

    w_i  <-  w_i * t_i / observed_i

Repeat. This is iterative proportional fitting, and it converges because raising
one player's weight lowers everyone else's share by construction.

WHY IT CANNOT BREAK ANYTHING ABOVE IT. A share is normalised within the lineup,
so scaling weights moves only the SPLIT of a team's rebounds, assists or shots —
never how many there are. Team totals, the anchor and every team-level
calibration are untouched by design, and the validation checks that rather than
assuming it.

Offensive and defensive allocations both draw from the player's OWN five, so a
correction solved against one opponent transfers to any other.

Output: data/parquet/share_cal.parquet
Usage: python scripts/159_calibrate_shares.py [--iters 4] [--sims 80]
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "parquet" / "share_cal.parquet"

# stat -> (box field, rate columns that feed the weight)
# One rebound factor: the engine books a single REB counter, so offensive and
# defensive shares cannot be observed apart. Splitting the observed total by the
# target proportions, as a first attempt did, makes the two ratios identical by
# construction and calibrates nothing.
STATS = ["USE", "REB", "AST", "STL", "BLK", "PF"]
DAMP = 0.7          # under-relaxation; full steps oscillate
CLIP = (0.35, 3.0)  # a weight may not run away on a thin sample


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=4)
    ap.add_argument("--sims", type=int, default=80)
    ap.add_argument("--teams", type=int, default=30)
    args = ap.parse_args()

    S = load("sim124", ROOT / "scripts" / "124_possession_sim.py")
    MU = load("mu154", ROOT / "scripts" / "154_matchup.py")
    g = pd.read_parquet(ROOT / "data/parquet/games.parquet")
    season = sorted(g.SEASON.unique())[-1]
    gs = g[g.SEASON == season]
    code = {}
    for r in gs.itertuples():
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

    net = {t: off[t] - dfn[t] for t in off}
    ref_id = sorted(net, key=lambda t: abs(net[t] - np.median(list(net.values()))))[0]
    ref = {v: k for k, v in code.items()}[ref_id]
    print(f"{season}: calibrating against {ref} (median net rating), "
          f"{args.iters} iterations x {args.sims} sims")

    cal = {}
    abbrs = [x for x in sorted(code) if x != ref][:args.teams]
    for it in range(args.iters):
        errs, obs = [], {}
        for abbr in abbrs:
            tid = code[abbr]
            sides = MU.build_sides(season, tid, ref_id, set(),
                                   rates=rates, rseason=rseason)
            aref = MU.prior_roster(season, tid, ref_id)
            sim.share_cal = cal
            _, box, _ = sim.simulate(sides["H"], sides["A"], tid, ref_id,
                                     n_sims=args.sims, seed=11,
                                     anchor=(mu0 + off[tid] - dfn[ref_id] + hca,
                                             mu0 + off[ref_id] - dfn[tid]),
                                     anchor_ref=aref)
            mins = {p["pid"]: p["minutes"] for p in sides["H"]}
            for pid, m in mins.items():
                if m < 6:
                    continue
                st = box["H"].get(pid)
                if st is None:
                    continue
                r = rates[pid]
                targets = {
                    "USE": (float(r.get("FG2A_36", 0)) + float(r.get("FG3A_36", 0)))
                           * m / 36.0,
                    "OREB": float(r.get("OREB_36", 0)) * m / 36.0,
                    "DREB": float(r.get("DREB_36", 0)) * m / 36.0,
                    "AST": float(r.get("AST_36", 0)) * m / 36.0,
                    "STL": float(r.get("STL_36", 0)) * m / 36.0,
                    "BLK": float(r.get("BLK_36", 0)) * m / 36.0,
                    "PF": float(r.get("PF_36", 0)) * m / 36.0}
                got = {"USE": float(st["FGA"].mean()),
                       "REB": float(st["REB"].mean()),
                       "AST": float(st["AST"].mean()),
                       "STL": float(st["STL"].mean()),
                       "BLK": float(st["BLK"].mean()),
                       "PF": float(st["PF"].mean())}
                targets["REB"] = targets["OREB"] + targets["DREB"]
                obs.setdefault(abbr, []).append((pid, targets, got))
        # ---- normalise targets to what the team ACTUALLY produced ----
        # A share cannot exceed the whole. If a team's total for a stat differs
        # from the sum of its players' rate-implied targets, those targets are
        # unreachable and the iteration chases them forever: the first attempt
        # stalled at 13% error with every factor drifting away from 1.0 (steals
        # to 0.63, rebounds to 1.34) because it was trying to fix a TOTAL with
        # weights that can only move a SPLIT. Rescaling the targets to the
        # observed total leaves a fixed point the weights can actually reach,
        # and the residual total error is a separate question for script 125.
        for abbr, recs in obs.items():
            for stat in STATS:
                tsum = sum(r[1].get(stat, 0.0) for r in recs)
                osum = sum(r[2].get(stat, 0.0) for r in recs)
                if tsum <= 1e-9 or osum <= 1e-9:
                    continue
                k = osum / tsum
                for pid, targets, got in recs:
                    t_, o_ = targets.get(stat, 0.0) * k, got.get(stat, 0.0)
                    if t_ < 0.15 or o_ <= 1e-9:
                        continue
                    cur = cal.get((int(pid), stat), 1.0)
                    cal[(int(pid), stat)] = float(
                        np.clip(cur * (t_ / o_) ** DAMP, *CLIP))
                    errs.append(abs(o_ / t_ - 1.0))
        print(f"  iteration {it + 1}: mean |error| {np.mean(errs) * 100:5.2f}%  "
              f"({len(cal):,} factors)", flush=True)

    rows = [{"PLAYER_ID": pid, "stat": stat, "cal": v}
            for (pid, stat), v in sorted(cal.items())]
    df = pd.DataFrame(rows)
    df.to_parquet(OUT, index=False)
    print(f"\nWrote {OUT}: {len(df):,} factors")
    print(f"  {'stat':<6}{'n':>6}{'mean':>8}{'p10':>8}{'p90':>8}")
    for stat in STATS:
        d = df[df.stat == stat]
        if len(d):
            print(f"  {stat:<6}{len(d):>6}{d.cal.mean():>8.3f}"
                  f"{d.cal.quantile(.1):>8.3f}{d.cal.quantile(.9):>8.3f}")


if __name__ == "__main__":
    main()
