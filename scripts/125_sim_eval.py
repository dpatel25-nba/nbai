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
import multiprocessing as mp
import os
import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MPG_FALLBACK = 0.83   # see the note at its use site
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


_G = {}


def _eval_game(idx):
    """Simulate one game. A Pool needs a module-level callable, so context
    arrives through the forked-in _G rather than through closure.

    The seed is a function of (base seed, game INDEX), never of a generator
    advanced in visit order: a pool does not guarantee that order, and
    order-dependent seeds would silently break the common random numbers that
    every paired comparison in this project relies on.
    """
    G = _G
    args, sim, S = G["args"], G["sim"], G["S"]
    gid = G["gids"][idx]
    rg = np.random.default_rng([args.seed, idx, 7])
    g = G["gs"][G["gs"].GAME_ID == gid].iloc[0]
    rr = G["pg"][G["pg"].GAME_ID == gid]

    sides, ok = {}, True
    for tag, tid in (("H", g.HOME_TEAM_ID), ("A", g.AWAY_TEAM_ID)):
        dd = rr[(rr.TEAM_ID == tid) & (rr.MIN > 0)]
        pl = []
        for r in dd.itertuples():
            pid = int(r.PLAYER_ID)
            mins = (float(r.MIN) if args.minutes == "actual"
                    else float(G["pmin"].get((gid, pid), np.nan)))
            if not np.isfinite(mins):
                # NO PROJECTION IS NOT NO PLAYER. Dropping him and rescaling the
                # rest to 240 team-minutes inflates everyone who remains: only
                # 9.34 of 10.69 players per team-game carry a props projection,
                # covering 217.8 of 241.3 actual minutes, so the rescale is a
                # factor of ~1.108. Worse, `occupancy` caps a player at one slot,
                # so the surplus cannot go to starters and lands on the
                # mid-rotation instead — which is precisely where the engine
                # appeared to over-predict by 1.0-1.4 points. Fall back to the
                # rate book's MPG, which is leakage-safe (prior seasons plus
                # in-season updates) and keeps the roster whole.
                # Scaled: an unprojected player's SEASON MPG overstates his role
                # in the specific game he was left out of (props omits exactly
                # the players whose minutes are erratic). Unscaled it puts the
                # roster at 246.0 team-minutes against a real 241.3, so the 240
                # rescale then DEFLATES everyone by 0.975 and starters lose most
                # in absolute points. 0.83 lands the roster on the real total.
                mins = MPG_FALLBACK * float(sim.rates[pid].get("MPG", 0.0) or 0.0)
                if not np.isfinite(mins) or mins <= 0:
                    continue
            pl.append({"pid": pid, "minutes": mins,
                       "started": int(isinstance(r.position, str)
                                      and bool(r.position.strip()))})
        if len(pl) < 6:
            ok = False
        tot = sum(q["minutes"] for q in pl)
        if tot > 0:
            for q in pl:
                q["minutes"] *= 240.0 / tot
        sides[tag] = pl
    if not ok:
        return None, []

    if G["defq_by_game"]:
        sim.defq = G["defq_by_game"].get(gid, {})
    fac = G["drift"].get(gid, {})
    if G["game_rates"] or fac:
        base = G["base_rates"]
        ov = dict(base)
        for tag in sides:
            for q in sides[tag]:
                merged = dict(base[q["pid"]])
                gr = G["game_rates"].get((gid, q["pid"]))
                if gr:
                    merged.update(gr)
                # league correction LAST, so it pins the in-season updated
                # rates and not merely the prior-season book
                ov[q["pid"]] = S.apply_drift(merged, fac)
        sim.rates = S.RateBook(ov, base.fallback)

    pace_ig = G["pace_ig"]
    res, box, _ = sim.simulate(
        sides["H"], sides["A"], g.HOME_TEAM_ID, g.AWAY_TEAM_ID,
        n_sims=args.sims,
        seed=int(np.random.default_rng([args.seed, idx]).integers(1 << 30)),
        anchor=G["anchors"].get(gid),
        pace_pair=(pace_ig.get((gid, g.HOME_TEAM_ID)),
                   pace_ig.get((gid, g.AWAY_TEAM_ID)))
        if (gid, g.HOME_TEAM_ID) in pace_ig else None)
    h, a = res["H"], res["A"]
    m = h - a
    # team-level box totals, so BOX REALISM is checked on the pipeline the
    # evaluation actually uses. Diagnosing free throws on the lean harness —
    # which omits in-season rate updating — produced a 2.9 FT/game "excess"
    # that the real pipeline does not necessarily have.
    box_tot = {}
    for _k in ("FTA", "FGA", "FG3M", "TOV", "AST", "REB", "PF"):
        box_tot["sim_" + _k] = float(sum(st[_k].mean() for t in ("H", "A")
                                         for _, st in box[t].items()))
    row = {"GAME_ID": gid, "p_home": float((m > 0).mean()), **box_tot,
           "pred_margin": float(m.mean()), "pred_total": float((h + a).mean()),
           "act_margin": float(g.MARGIN), "act_total": float(g.TOTAL),
           "home_win": int(g.HOME_WIN)}

    plines = []
    actual_pts = {int(r.PLAYER_ID): r.points for r in rr.itertuples()}
    for tag in ("H", "A"):
        for pid, st in box[tag].items():
            if pid not in actual_pts:
                continue
            dpt = st["PTS"]
            plines.append({"GAME_ID": gid, "PLAYER_ID": pid,
                           "pred": float(dpt.mean()),
                           "actual": float(actual_pts[pid]),
                           "p10": float(np.percentile(dpt, 10)),
                           "p90": float(np.percentile(dpt, 90)),
                           "p25": float(np.percentile(dpt, 25)),
                           "p75": float(np.percentile(dpt, 75)),
                           "pit": float((dpt < actual_pts[pid]).mean()
                                        + rg.random()
                                        * (dpt == actual_pts[pid]).mean()),
                           "p_zero": float((dpt <= 0.5).mean()),
                           "sim_min": float(np.percentile(dpt, 0)),
                           "predmin": float(G["pmin"].get((gid, pid), np.nan)),
                           "engine": G["ppts"].get((gid, pid), np.nan)})
    return row, plines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=120)
    ap.add_argument("--sims", type=int, default=150)
    ap.add_argument("--minutes", choices=["projected", "actual"], default="projected")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--dump", default="", help="write per-game rows here for "
                    "paired comparison between configurations")
    ap.add_argument("--no-drift", action="store_true",
                    help="disable the league drift correction (script 153)")
    ap.add_argument("--lineup-drift", type=float, default=None,
                    help="override LINEUP_DRIFT in the engine")
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

    if args.lineup_drift is not None:
        S.LINEUP_DRIFT = float(args.lineup_drift)
        print(f"LINEUP_DRIFT overridden to {S.LINEUP_DRIFT}", flush=True)
    drift = {} if args.no_drift else S.league_drift(SEASON)
    print(f"league drift factors loaded for {len(drift):,} games", flush=True)
    _G.update(dict(drift=drift,
                   gs=gs, pg=pg, sim=sim, S=S, args=args, pmin=pmin, ppts=ppts,
                   anchors=anchors, pace_ig=pace_ig, game_rates=game_rates,
                   base_rates=base_rates, defq_by_game=defq_by_game, gids=gids))
    workers = max(1, min((os.cpu_count() or 2) - 2, len(gids)))
    if workers > 1:
        # fork so the built Simulator is inherited copy-on-write; spawn would
        # re-import this module and rebuild projections in every worker
        with mp.get_context("fork").Pool(workers) as pool:
            out = pool.map(_eval_game, range(len(gids)),
                           chunksize=max(1, len(gids) // (workers * 4)))
    else:
        out = [_eval_game(i) for i in range(len(gids))]
    rows = [r for r, _ in out if r is not None]
    plines = [q for _, ps in out for q in ps]
    print(f"  simulated {len(rows)}/{len(gids)} games on {workers} workers",
          flush=True)

    d = pd.DataFrame(rows)
    pl = pd.DataFrame(plines)
    print(f"\n{'='*70}\nSIMULATOR EVALUATION — {SEASON}, {len(d)} games, "
          f"{args.sims} sims each, minutes={args.minutes}\n{'='*70}")

    if args.dump:
        d.to_parquet(args.dump, index=False)
        print(f"per-game rows -> {args.dump}", flush=True)
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

    # Machine-readable summary, so the website cannot silently show numbers
    # from an older model than the one in the repository.
    SUMMARY = {"season": SEASON, "games": int(len(d)),
               "sims": int(args.sims), "minutes": args.minutes}

    print("\n1. WIN PROBABILITY")
    print(f"   {'model':<26}{'acc':>8}{'logloss':>10}{'brier':>9}")
    for nm, col in [("possession sim", "p_home"),
                    ("Elo baseline", "p_elo"), ("Mode-1 sim", "p_s1")]:
        v = d[col].dropna()
        if len(v) > 10:
            acc, ll, br = metrics(v.to_numpy(), d.loc[v.index, "home_win"].to_numpy())
            print(f"   {nm:<26}{acc:>8.3f}{ll:>10.4f}{br:>9.4f}")
            SUMMARY[{"possession sim": "sim", "Elo baseline": "elo",
                     "Mode-1 sim": "mode1"}[nm]] = {
                "acc": round(acc, 4), "logloss": round(ll, 4), "brier": round(br, 4)}

    # The width-recalibration that used to sit here is gone. Fitted on half the
    # games it produced sd = 9.00, 11.00, 13.50 and 17.75 across four runs, and
    # made held-out log-loss WORSE than the raw simulator in two of them. A
    # parameter that swings 2x between samples of the same season is fitting
    # noise. Mode-1 and Elo own win probability; the simulator reports its raw
    # number and does not pretend to compete there.
    print("\n2. SCORE PREDICTION (MAE)")
    print(f"   {'model':<26}{'margin':>9}{'total':>9}")
    print(f"   {'possession sim (new)':<26}"
          f"{(d.pred_margin - d.act_margin).abs().mean():>9.2f}"
          f"{(d.pred_total - d.act_total).abs().mean():>9.2f}")
    v = d.dropna(subset=["m_s1"])
    if len(v) > 10:
        print(f"   {'Mode-1 sim':<26}{(v.m_s1 - v.act_margin).abs().mean():>9.2f}"
              f"{(v.t_s1 - v.act_total).abs().mean():>9.2f}")
    SUMMARY["margin_mae"] = round(float((d.pred_margin - d.act_margin).abs().mean()), 3)
    SUMMARY["total_mae"] = round(float((d.pred_total - d.act_total).abs().mean()), 3)
    if len(v) > 10:
        SUMMARY["mode1_margin_mae"] = round(float((v.m_s1 - v.act_margin).abs().mean()), 3)
        SUMMARY["mode1_total_mae"] = round(float((v.t_s1 - v.act_total).abs().mean()), 3)
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
        SUMMARY["player_pts_mae"] = round(float((e.pred - e.actual).abs().mean()), 3)
        SUMMARY["props_pts_mae"] = round(float((e.engine - e.actual).abs().mean()), 3)

    # ---- box realism on the same games ----
    BOXMAP = {"FTA": "freeThrowsAttempted", "FGA": "fieldGoalsAttempted",
              "FG3M": "threePointersMade", "TOV": "turnovers",
              "AST": "assists", "REB": "reboundsTotal", "PF": "foulsPersonal"}
    try:
        _bp = pd.read_parquet(PG, columns=["GAME_ID"] + list(BOXMAP.values()))
        _bp = _bp[_bp.GAME_ID.isin(set(d.GAME_ID))]
        _act = _bp.groupby("GAME_ID").sum().mean()
        print("\n3b. BOX REALISM  (per game, both teams)")
        print(f"   {'stat':<8}{'sim':>9}{'actual':>9}{'ratio':>8}")
        for k, col in BOXMAP.items():
            sk = "sim_" + k
            if sk not in d.columns:
                continue
            sv, av = float(d[sk].mean()), float(_act[col])
            print(f"   {k:<8}{sv:>9.2f}{av:>9.2f}{sv/av if av else float('nan'):>8.3f}")
            SUMMARY.setdefault("box", {})[k] = {"sim": round(sv, 2),
                                                "actual": round(av, 2),
                                                "ratio": round(sv / av, 3) if av else None}
    except (KeyError, ValueError) as e:
        print(f"\n3b. BOX REALISM unavailable ({e})")

    print("\n4. DISTRIBUTIONAL CALIBRATION  (the simulator's real job)")
    c80 = ((pl.actual >= pl.p10) & (pl.actual <= pl.p90)).mean()
    c50 = ((pl.actual >= pl.p25) & (pl.actual <= pl.p75)).mean()
    # Points are INTEGERS and p10/p90 usually are too, so a player whose actual
    # lands exactly on a band edge counts as covered — and 14-15% of them do.
    # That inflated these numbers by ~7 points and made the intervals look far
    # too wide for a long time. The continuity-corrected figures below add
    # U(-0.5, 0.5) to the outcome, which is how a discrete target must be
    # scored against a continuous band, and they are the honest ones.
    _rj = np.random.default_rng(4242)
    _aj = pl.actual.to_numpy() + _rj.uniform(-0.5, 0.5, len(pl))
    j80 = float(((_aj >= pl.p10.to_numpy()) & (_aj <= pl.p90.to_numpy())).mean())
    j50 = float(((_aj >= pl.p25.to_numpy()) & (_aj <= pl.p75.to_numpy())).mean())
    SUMMARY["cover80"] = round(j80 * 100, 1)
    SUMMARY["cover50"] = round(j50 * 100, 1)
    print(f"   player points 80% interval coverage : {j80*100:5.1f}%   (target 80%)"
          f"   [{c80*100:.1f}% counting band-edge ties as covered]")
    print(f"   player points 50% interval coverage : {j50*100:5.1f}%   (target 50%)"
          f"   [{c50*100:.1f}% counting band-edge ties as covered]")
    hist = np.histogram(pl.pit, bins=10, range=(0, 1))[0] / len(pl)
    print(f"   PIT histogram (flat = calibrated)   : "
          + " ".join(f"{x*100:.0f}" for x in hist))
    SUMMARY["pit_dev"] = round(float(np.abs(hist - 0.1).sum()), 4)
    import json as _json
    _sp = ROOT / "data" / "features" / "sim_eval_summary.json"
    _sp.parent.mkdir(parents=True, exist_ok=True)
    _sp.write_text(_json.dumps(SUMMARY, indent=2))
    print(f"   summary -> {_sp}")
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
            # Jitter the OUTCOME, not the score. Points are integers, so a
            # deterministic band's coverage is a STEP function: 15% of players
            # sit exactly on p10/p90, and shrinking the band by any epsilon
            # drops that entire atom at once — the 50% band fell 59.9% -> 44.6%
            # on a width change of 0.1 points. No band can hit 50% exactly.
            # Adding U(-0.5, 0.5) to the outcome makes it continuous at integer
            # resolution, which is the standard treatment for discrete targets
            # and the only way conformal's guarantee means anything here.
            rj = np.random.default_rng(int(lvl * 1000))
            a_cal = cal.actual.to_numpy() + rj.uniform(-0.5, 0.5, len(cal))
            a_tst = tst.actual.to_numpy() + rj.uniform(-0.5, 0.5, len(tst))
            E = np.maximum(cal[lo_c].to_numpy() - a_cal, a_cal - cal[hi_c].to_numpy())
            n = len(E)
            neg, zero = float((E < 0).mean()), float((E == 0).mean())
            # CONTINUITY CORRECTION. Points are integers and the simulated
            # percentiles usually are too, so every player whose actual lands
            # exactly on p10 or p90 scores exactly 0. That atom holds 14-15% of
            # the mass, and the target quantile falls INSIDE it — which is why
            # this step returned q=+0.00 and did nothing at all while the bands
            # over-covered by 7 points. Conformal prediction assumes a
            # continuous score; jittering by U(-0.5, 0.5) restores that without
            # changing the distribution at integer resolution.
            q = float(np.quantile(E, min(1.0, np.ceil((n + 1) * lvl) / n)))
            print(f"   [{int(lvl*100)}%] conformity score: {neg*100:.1f}% below 0, "
                  f"{zero*100:.1f}% EXACTLY 0, {(1-neg-zero)*100:.1f}% above"
                  f"  -> q={q:+.2f} after continuity correction")
            raw = float(((a_tst >= tst[lo_c].to_numpy())
                         & (a_tst <= tst[hi_c].to_numpy())).mean())
            con = float(((a_tst >= tst[lo_c].to_numpy() - q)
                         & (a_tst <= tst[hi_c].to_numpy() + q)).mean())
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
