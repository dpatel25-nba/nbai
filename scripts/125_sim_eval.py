"""
Simulator evaluation on 2025-26 — the honest test.

Runs the possession simulator (124) across many 2025-26 games and scores it
against the models already in the repo, on four fronts:

  1. WIN PROBABILITY   accuracy / log-loss / Brier vs the Elo baseline (11) and
                       the Mode-1 distributional sim (50).
  2. SCORE             margin and total MAE vs Mode-1.
  3. PLAYER LINES      points MAE vs the production props engine (71), which is
                       the incumbent to beat.
  4. CALIBRATION       the metric that actually matters for a simulator: are the
                       predicted DISTRIBUTIONS right? 80%/50% interval coverage
                       and PIT uniformity. A simulator whose mean is average but
                       whose intervals are honest is more useful for props and
                       totals than a sharper point estimate with no uncertainty.

MINUTES SOURCE IS THE KEY HONESTY KNOB. Reading minutes off the box score is
hindsight and inflates everything downstream, exactly like the actual-vs-
projected gap script 64 measured. Default here is PROJECTED minutes from the
production props model (leakage-safe). `--minutes actual` reports the idealized
upper bound for comparison, and both are printed so the gap is explicit.

Who is ACTIVE is taken as given (the real deployment input). That is an
assumption, not a prediction — your audit found ~20% of absences are surprise
scratches unknowable pre-game, so treat the roster as the "perfect injury report"
case and read the numbers accordingly.

Usage:
  python scripts/125_sim_eval.py --games 120 --sims 150
  python scripts/125_sim_eval.py --games 60 --sims 200 --minutes actual
"""

from __future__ import annotations

import argparse
import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GAMES = ROOT / "data" / "parquet" / "games.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
PROPS = ROOT / "data" / "features" / "props_predictions.parquet"
ELO = ROOT / "data" / "features" / "elo_predictions.parquet"
SIM1 = ROOT / "data" / "features" / "sim_mode1_predictions.parquet"
TMPL = ROOT / "data" / "parquet" / "rotation_templates.parquet"
AFF = ROOT / "data" / "parquet" / "assignment_affinity.parquet"
DQ = ROOT / "data" / "parquet" / "defender_quality_v2.parquet"
DEFAULT_SEASON = "2025-26"


def load_sim_module():
    spec = importlib.util.spec_from_file_location(
        "sim124", ROOT / "scripts" / "124_possession_sim.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def metrics(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return (((p > 0.5).astype(int) == y).mean(),
            -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)),
            np.mean((p - y) ** 2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=120)
    ap.add_argument("--sims", type=int, default=150)
    ap.add_argument("--minutes", choices=["projected", "actual"], default="projected")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--season", default=DEFAULT_SEASON,
                    help="evaluate on a season the parameters were NOT tuned on")
    ap.add_argument("--ingame", choices=["on", "off"], default="on",
                    help="use in-season updated rates (script 131) vs prior-season only")
    args = ap.parse_args()

    S = load_sim_module()
    SEASON = args.season
    games = pd.read_parquet(GAMES)
    gs = games[(games.SEASON == SEASON) & (games.SEASON_TYPE == "Regular Season")]
    rng = np.random.default_rng(args.seed)
    gids = list(gs.GAME_ID.sample(min(args.games, len(gs)), random_state=args.seed))

    print(f"Loading projections for {SEASON}...", flush=True)
    rates, pos = S.build_rates(SEASON)
    tmpl = pd.read_parquet(TMPL)
    aff = pd.read_parquet(AFF).set_index("dpos")[S.POSITIONS].to_numpy()
    dq_raw = S.defensive_index(SEASON)
    defq_by_game = {}
    if dq_raw and isinstance(next(iter(dq_raw)), tuple):
        for (gg, pp), vv in dq_raw.items():
            defq_by_game.setdefault(gg, {})[pp] = vv
        defq = {}
    else:
        defq = dq_raw
    if not defq and not defq_by_game:
        dq = pd.read_parquet(DQ)
        dq = dq[dq.SEASON == SEASON]
        defq = {int(r.PLAYER_ID): float(r.DEF_RATING) for r in dq.itertuples()}
    pace_map, lg_pace = S.team_pace(SEASON)
    w3 = pd.read_parquet(ROOT / "data" / "parquet" / "player_seasons_war_v3.parquet")
    w3 = w3[w3.SEASON == SEASON]
    bpm = {int(r.PLAYER_ID): float(r.BPM3) for r in w3.itertuples()}
    sim = S.Simulator(rates, pos, tmpl, aff, defq, pace_map, lg_pace, bpm,
                      S.minutes_ratio_pools(SEASON), S.usage_ratio_pool(SEASON))

    pg = pd.read_parquet(PG, columns=["GAME_ID", "TEAM_ID", "PLAYER_ID", "MIN",
                                      "position", "points"])
    pg = pg[pg.GAME_ID.isin(gids)]
    props = pd.read_parquet(PROPS)
    props = props[props.SEASON == SEASON]
    s1a = pd.read_parquet(SIM1)[["GAME_ID", "MU_HOME", "MU_AWAY"]]
    anchors = {r.GAME_ID: (r.MU_HOME, r.MU_AWAY) for r in s1a.itertuples()}
    # in-season empirical-Bayes rates (script 131): the simulator otherwise sees
    # only prior seasons and discards everything observed since October
    IGR = ROOT / "data" / "parquet" / "ingame_rates" / f"{SEASON}.parquet"
    game_rates = {}
    if args.ingame == "on" and IGR.exists():
        ig = pd.read_parquet(IGR)
        rc = [c for c in ig.columns if c not in ("GAME_ID", "PLAYER_ID", "SEASON",
                                                 "m_todate")]
        for r in ig.itertuples():
            game_rates[(r.GAME_ID, int(r.PLAYER_ID))] = {c: getattr(r, c) for c in rc}
        print(f"in-season rates loaded: {len(game_rates):,} player-games", flush=True)
    pace_ig = S.team_pace_ingame(SEASON)
    base_rates = sim.rates
    pmin = {(r.GAME_ID, int(r.PLAYER_ID)): r.pred_min for r in props.itertuples()}
    ppts = {(r.GAME_ID, int(r.PLAYER_ID)): r.pred_points for r in props.itertuples()}

    rows, plines = [], []
    for n, gid in enumerate(gids, 1):
        g = gs[gs.GAME_ID == gid].iloc[0]
        rr = pg[pg.GAME_ID == gid]
        sides, ok = {}, True
        for tag, tid in (("H", g.HOME_TEAM_ID), ("A", g.AWAY_TEAM_ID)):
            d = rr[(rr.TEAM_ID == tid) & (rr.MIN > 0)]
            pl = []
            for r in d.itertuples():
                pid = int(r.PLAYER_ID)
                mins = (float(r.MIN) if args.minutes == "actual"
                        else float(pmin.get((gid, pid), np.nan)))
                if not np.isfinite(mins):
                    continue
                pl.append({"pid": pid, "minutes": mins,
                           "started": int(isinstance(r.position, str) and bool(r.position.strip()))})
            if len(pl) < 6:
                ok = False
            # rescale to a legal 240 team-minutes
            tot = sum(p["minutes"] for p in pl)
            if tot > 0:
                for p in pl:
                    p["minutes"] *= 240.0 / tot
            sides[tag] = pl
        if not ok:
            continue

        if defq_by_game:
            sim.defq = defq_by_game.get(gid, {})
        if game_rates:
            ov = dict(base_rates)
            for tag in sides:
                for pl in sides[tag]:
                    gr = game_rates.get((gid, pl["pid"]))
                    if gr:
                        merged = dict(base_rates[pl["pid"]])
                        merged.update(gr)
                        ov[pl["pid"]] = merged
            sim.rates = S.RateBook(ov, base_rates.fallback)
        res, box, _ = sim.simulate(sides["H"], sides["A"], g.HOME_TEAM_ID,
                                   g.AWAY_TEAM_ID, n_sims=args.sims,
                                   seed=int(rng.integers(1 << 30)),
                                   anchor=anchors.get(gid),
                                   pace_pair=(pace_ig.get((gid, g.HOME_TEAM_ID)),
                                              pace_ig.get((gid, g.AWAY_TEAM_ID)))
                                   if (gid, g.HOME_TEAM_ID) in pace_ig else None)
        h, a = res["H"], res["A"]
        m = h - a
        rows.append({"GAME_ID": gid, "p_home": float((m > 0).mean()),
                     "pred_margin": float(m.mean()), "pred_total": float((h + a).mean()),
                     "act_margin": float(g.MARGIN), "act_total": float(g.TOTAL),
                     "home_win": int(g.HOME_WIN)})
        actual_pts = {int(r.PLAYER_ID): r.points for r in rr.itertuples()}
        for tag in ("H", "A"):
            for pid, st in box[tag].items():
                if pid in actual_pts:
                    d = st["PTS"]
                    plines.append({"GAME_ID": gid, "PLAYER_ID": pid,
                                   "pred": float(d.mean()), "actual": float(actual_pts[pid]),
                                   "p10": float(np.percentile(d, 10)),
                                   "p90": float(np.percentile(d, 90)),
                                   "p25": float(np.percentile(d, 25)),
                                   "p75": float(np.percentile(d, 75)),
                                   "pit": float((d < actual_pts[pid]).mean()
                                                + np.random.random()
                                                * (d == actual_pts[pid]).mean()),
                                   "p_zero": float((d <= 0.5).mean()),
                                   "sim_min": float(np.percentile(d, 0)),
                                   "predmin": float(pmin.get((gid, pid), np.nan)),
                                   "engine": ppts.get((gid, pid), np.nan)})
        if n % 10 == 0:
            print(f"  simulated {n}/{len(gids)} games", flush=True)

    d = pd.DataFrame(rows)
    pl = pd.DataFrame(plines)
    print(f"\n{'='*70}\nSIMULATOR EVALUATION — {SEASON}, {len(d)} games, "
          f"{args.sims} sims each, minutes={args.minutes}\n{'='*70}")

    y = d.home_win.to_numpy()
    elo = pd.read_parquet(ELO)[["GAME_ID", "P_HOME"]].rename(columns={"P_HOME": "p_elo"})
    s1 = pd.read_parquet(SIM1)[["GAME_ID", "P_HOME", "pred_margin", "pred_total"]].rename(
        columns={"P_HOME": "p_s1", "pred_margin": "m_s1", "pred_total": "t_s1"})
    d = d.merge(elo, on="GAME_ID", how="left").merge(s1, on="GAME_ID", how="left")

    # The possession engine is over-dispersed on margin: per-game sd ~16.7
    # against an actual conditional sd near 14.4, because possessions are drawn
    # independently while real basketball has stabilising feedback (leads relax
    # defences, pace adjusts, garbage time compresses). Reading P(margin>0) off
    # that distribution yields UNDER-CONFIDENT probabilities. Recalibrate the
    # width on held-out games, as sim_mode1 already does with phi(m/MARGIN_SD).
    import math
    gl2 = d.GAME_ID.unique()
    rc2 = np.random.default_rng(1)
    cg = set(rc2.choice(gl2, max(1, len(gl2) // 2), replace=False))
    cal = d[d.GAME_ID.isin(cg)]
    best_sd, best_ll = None, None
    for sd_try in np.arange(9.0, 20.1, 0.25):
        pc = np.array([0.5 * (1 + math.erf(m / (sd_try * math.sqrt(2))))
                       for m in cal.pred_margin])
        pc = np.clip(pc, 1e-6, 1 - 1e-6)
        ll = -np.mean(cal.home_win * np.log(pc) + (1 - cal.home_win) * np.log(1 - pc))
        if best_ll is None or ll < best_ll:
            best_sd, best_ll = float(sd_try), float(ll)
    d["p_cal"] = [0.5 * (1 + math.erf(m / (best_sd * math.sqrt(2))))
                  for m in d.pred_margin]
    d["_held"] = ~d.GAME_ID.isin(cg)

    print("\n1. WIN PROBABILITY")
    print(f"   {'model':<26}{'acc':>8}{'logloss':>10}{'brier':>9}")
    for nm, col in [("possession sim (raw)", "p_home"),
                    ("possession sim (calibrated)", "p_cal"),
                    ("Elo baseline", "p_elo"), ("Mode-1 sim", "p_s1")]:
        v = d[col].dropna()
        if len(v) > 10:
            acc, ll, br = metrics(v.to_numpy(), d.loc[v.index, "home_win"].to_numpy())
            print(f"   {nm:<26}{acc:>8.3f}{ll:>10.4f}{br:>9.4f}")

    h = d[d._held]
    if len(h) > 20:
        acc, ll, br = metrics(h.p_cal.to_numpy(), h.home_win.to_numpy())
        print(f"   {'  (calibrated, HELD-OUT half only)':<26}{acc:>8.3f}{ll:>10.4f}{br:>9.4f}"
              f"   sd={best_sd:.2f}")

    print("\n2. SCORE PREDICTION (MAE)")
    print(f"   {'model':<26}{'margin':>9}{'total':>9}")
    print(f"   {'possession sim (new)':<26}"
          f"{(d.pred_margin - d.act_margin).abs().mean():>9.2f}"
          f"{(d.pred_total - d.act_total).abs().mean():>9.2f}")
    v = d.dropna(subset=["m_s1"])
    if len(v) > 10:
        print(f"   {'Mode-1 sim':<26}{(v.m_s1 - v.act_margin).abs().mean():>9.2f}"
              f"{(v.t_s1 - v.act_total).abs().mean():>9.2f}")
    print(f"   mean predicted total {d.pred_total.mean():.1f} vs actual {d.act_total.mean():.1f} "
          f"(bias {d.pred_total.mean()-d.act_total.mean():+.1f})")

    print(f"\n3. PLAYER POINTS ({len(pl):,} player-games)")
    print(f"   {'model':<26}{'MAE':>9}")
    print(f"   {'possession sim (new)':<26}{(pl.pred - pl.actual).abs().mean():>9.3f}")
    e = pl.dropna(subset=["engine"])
    if len(e) > 10:
        print(f"   {'props engine (71)':<26}{(e.engine - e.actual).abs().mean():>9.3f}"
              f"   [same {len(e):,} rows]")
        print(f"   {'possession sim, same rows':<26}{(e.pred - e.actual).abs().mean():>9.3f}")

    print("\n4. DISTRIBUTIONAL CALIBRATION  (the simulator's real job)")
    c80 = ((pl.actual >= pl.p10) & (pl.actual <= pl.p90)).mean()
    c50 = ((pl.actual >= pl.p25) & (pl.actual <= pl.p75)).mean()
    print(f"   player points 80% interval coverage : {c80*100:5.1f}%   (target 80%)")
    print(f"   player points 50% interval coverage : {c50*100:5.1f}%   (target 50%)")
    hist = np.histogram(pl.pit, bins=10, range=(0, 1))[0] / len(pl)
    print(f"   PIT histogram (flat = calibrated)   : "
          + " ".join(f"{x*100:.0f}" for x in hist))
    print(f"   PIT deviation from uniform          : {np.abs(hist - 0.1).sum():.3f}"
          f"   (0 = perfect)")
    # ---- split-conformal calibration (CQR) ----
    # The raw bands come from a hand-tuned USE_SHRINK. Conformalizing replaces
    # that with a finite-sample GUARANTEE: score each calibration point by how
    # far outside the band it fell, take the (1-alpha) quantile of those scores,
    # and widen the band by it. Coverage then holds regardless of whether the
    # simulator's shape is right. Calibration and test games are disjoint.
    gl = pl.GAME_ID.unique()
    rc = np.random.default_rng(0)
    calg = set(rc.choice(gl, max(1, len(gl) // 2), replace=False))
    cal = pl[pl.GAME_ID.isin(calg)]
    tst = pl[~pl.GAME_ID.isin(calg)]
    print("\n   --- conformal prediction (split-CQR) ---")
    if len(cal) > 50 and len(tst) > 50:
        for lvl, lo_c, hi_c in [(0.80, "p10", "p90"), (0.50, "p25", "p75")]:
            E = np.maximum(cal[lo_c] - cal.actual, cal.actual - cal[hi_c])
            n = len(E)
            q = float(np.quantile(E, min(1.0, np.ceil((n + 1) * lvl) / n)))
            raw = ((tst.actual >= tst[lo_c]) & (tst.actual <= tst[hi_c])).mean()
            con = ((tst.actual >= tst[lo_c] - q) & (tst.actual <= tst[hi_c] + q)).mean()
            wr = (tst[hi_c] - tst[lo_c]).mean()
            print(f"   {int(lvl*100)}% band: raw {raw*100:5.1f}%  ->  conformal "
                  f"{con*100:5.1f}%   (target {int(lvl*100)}%)   "
                  f"width {wr:.1f} -> {wr + 2*q:.1f} pts   q={q:+.2f}")
    else:
        print("   (not enough games to split for calibration)")

    print("\n   --- lower-tail diagnostic ---")
    pl["bin0"] = pl.pit <= 0.0
    print(f"   player-games with actual == 0 pts      : {(pl.actual==0).mean()*100:5.1f}%")
    print(f"   sim mean P(0 pts)                      : {pl.p_zero.mean()*100:5.1f}%")
    pl["mb"] = pd.cut(pl.predmin, [0, 8, 14, 20, 28, 48])
    t = pl.groupby("mb", observed=True).agg(n=("bin0", "size"), bin0=("bin0", "mean"),
                                            act0=("actual", lambda s: (s == 0).mean()),
                                            simP0=("p_zero", "mean"))
    print(f"   {'proj min':<12}{'n':>7}{'PIT=0 share':>13}{'actual==0':>12}{'sim P(0)':>11}")
    for i, r in t.iterrows():
        print(f"   {str(i):<12}{int(r.n):>7}{r.bin0*100:>12.1f}%{r.act0*100:>11.1f}%{r.simP0*100:>10.1f}%")
    mb = d.pred_margin - d.act_margin
    print(f"   margin residual sd {mb.std():.1f} pts")
    return d, pl


if __name__ == "__main__":
    main()
