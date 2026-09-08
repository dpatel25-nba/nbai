"""
Where does the simulator's excess spread come from? A variance budget.

The engine's held-out point predictions are competitive, but its win
probabilities only TIE the top-down baseline instead of beating it. The reason
is dispersion: the predictive margin distribution is wider than the engine's own
accuracy justifies. A model that says "margin 4 +- 16" when its realised error is
13.5 is under-confident, and log-loss punishes that even when the mean is right.

THE HONEST TARGET is not the league's raw margin sd. It is the sd of the
engine's OWN residuals. If the sim's forecast is unbiased, a calibrated
predictive sd equals the RMSE of (actual - predicted). Anything above that is
manufactured noise. Comparing instead to the league-wide margin sd would be
wrong, because that number also contains the spread between good and bad
matchups, which a per-game forecast is supposed to predict rather than sample.

METHOD: ablation. Every stochastic source is switched off one at a time and the
change in predictive sd is recorded, on the SAME games with the SAME seeds so
the comparison is paired. What remains with everything off is the irreducible
possession-level binomial floor.

  shared pace draw     PACE_SD          possessions, common to both teams
  shared shooting      SHOOT_SD_SHARED  game-level hot/cold, common to both
  team shooting        SHOOT_SD_TEAM    one team runs hot independently
  usage variation      USE_SHRINK       who takes the shots on the night
  minutes jitter       MIN_JITTER_SHRINK  rotation uncertainty
  binomial floor       (all off)        shot-by-shot randomness alone

Sources are also removed CUMULATIVELY, because the ablations are not additive in
variance once the anchor rescales the level.

Usage: python scripts/143_variance_budget.py [--games 120] [--sims 80]
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MPG_FALLBACK = 0.83   # see the note at its use site
GAMES = ROOT / "data" / "parquet" / "games.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
PROPS = ROOT / "data" / "features" / "props_predictions.parquet"
SIM1 = ROOT / "data" / "features" / "sim_mode1_predictions.parquet"
TMPL = ROOT / "data" / "parquet" / "rotation_templates.parquet"
AFF = ROOT / "data" / "parquet" / "assignment_affinity.parquet"
DQ = ROOT / "data" / "parquet" / "defender_quality_v2.parquet"


def load_sim():
    spec = importlib.util.spec_from_file_location(
        "sim", ROOT / "scripts" / "124_possession_sim.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def build(S, season, n_games, seed):
    """Assemble the same inputs script 125 uses, for a sample of games."""
    games = pd.read_parquet(GAMES)
    gs = games[(games.SEASON == season) & (games.SEASON_TYPE == "Regular Season")]
    gids = list(gs.GAME_ID.sample(min(n_games, len(gs)), random_state=seed))
    rates, pos = S.build_rates(season)
    tmpl = pd.read_parquet(TMPL)
    aff = pd.read_parquet(AFF).set_index("dpos")[S.POSITIONS].to_numpy()
    dq_raw = S.defensive_index(season)
    defq_by_game, defq = {}, {}
    if dq_raw and isinstance(next(iter(dq_raw)), tuple):
        for (gg, pp), vv in dq_raw.items():
            defq_by_game.setdefault(gg, {})[pp] = vv
    else:
        defq = dq_raw
    if not defq and not defq_by_game:
        dq = pd.read_parquet(DQ)
        dq = dq[dq.SEASON == season]
        defq = {int(r.PLAYER_ID): float(r.DEF_RATING) for r in dq.itertuples()}
    pace_map, lg_pace = S.team_pace(season)
    w3 = pd.read_parquet(ROOT / "data" / "parquet" / "player_seasons_war_v3.parquet")
    w3 = w3[w3.SEASON == season]
    bpm = {int(r.PLAYER_ID): float(r.BPM3) for r in w3.itertuples()}
    sim = S.Simulator(rates, pos, tmpl, aff, defq, pace_map, lg_pace, bpm,
                      S.minutes_ratio_pools(season), S.usage_ratio_pool(season))

    pg = pd.read_parquet(PG, columns=["GAME_ID", "TEAM_ID", "PLAYER_ID", "MIN",
                                      "position", "points"])
    pg = pg[pg.GAME_ID.isin(gids)]
    props = pd.read_parquet(PROPS)
    props = props[props.SEASON == season]
    pmin = {(r.GAME_ID, int(r.PLAYER_ID)): r.pred_min for r in props.itertuples()}
    s1a = pd.read_parquet(SIM1)[["GAME_ID", "MU_HOME", "MU_AWAY"]]
    anchors = {r.GAME_ID: (r.MU_HOME, r.MU_AWAY) for r in s1a.itertuples()}
    pace_ig = S.team_pace_ingame(season)

    jobs = []
    for gid in gids:
        g = gs[gs.GAME_ID == gid].iloc[0]
        rr = pg[pg.GAME_ID == gid]
        sides, ok = {}, True
        for tag, tid in (("H", g.HOME_TEAM_ID), ("A", g.AWAY_TEAM_ID)):
            d = rr[(rr.TEAM_ID == tid) & (rr.MIN > 0)]
            pl = []
            for r in d.itertuples():
                pid = int(r.PLAYER_ID)
                mins = float(pmin.get((gid, pid), np.nan))
                if not np.isfinite(mins):
                    # see the note in 125: dropping unprojected players and
                    # rescaling to 240 inflates the rest by ~11%
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
            tot = sum(p["minutes"] for p in pl)
            if tot > 0:
                for p in pl:
                    p["minutes"] *= 240.0 / tot
            sides[tag] = pl
        if not ok:
            continue
        jobs.append({"gid": gid, "sides": sides, "hid": g.HOME_TEAM_ID,
                     "aid": g.AWAY_TEAM_ID, "anchor": anchors.get(gid),
                     "act_margin": float(g.MARGIN), "act_total": float(g.TOTAL),
                     "home_win": int(g.HOME_WIN), "defq": defq_by_game.get(gid),
                     "pace": ((pace_ig.get((gid, g.HOME_TEAM_ID)),
                               pace_ig.get((gid, g.AWAY_TEAM_ID)))
                              if (gid, g.HOME_TEAM_ID) in pace_ig else None)})
    return sim, jobs, defq_by_game


def run(sim, jobs, defq_by_game, n_sims, seed):
    """Predictive moments plus the realised residuals, on one configuration."""
    rng = np.random.default_rng(seed)
    mv, tv, sv, cs, pm, pt, am, at, hw = [], [], [], [], [], [], [], [], []
    for j in jobs:
        if defq_by_game:
            sim.defq = j["defq"] or {}
        res, _, _ = sim.simulate(j["sides"]["H"], j["sides"]["A"], j["hid"], j["aid"],
                                 n_sims=n_sims, seed=int(rng.integers(1 << 30)),
                                 anchor=j["anchor"], pace_pair=j["pace"])
        h, a = res["H"], res["A"]
        mv.append(np.var(h - a, ddof=1))
        tv.append(np.var(h + a, ddof=1))
        sv.append((np.var(h, ddof=1) + np.var(a, ddof=1)) / 2.0)
        if np.std(h) > 1e-9 and np.std(a) > 1e-9:
            cs.append(np.corrcoef(h, a)[0, 1])
        pm.append(float((h - a).mean()))
        pt.append(float((h + a).mean()))
        am.append(j["act_margin"])
        at.append(j["act_total"])
        hw.append(j["home_win"])
    pm, pt, am, at = map(np.asarray, (pm, pt, am, at))
    return {"margin_sd": float(np.sqrt(np.mean(mv))),
            "total_sd": float(np.sqrt(np.mean(tv))),
            "team_sd": float(np.sqrt(np.mean(sv))),
            "corr": float(np.mean(cs)) if cs else float("nan"),
            "resid_margin": float(np.sqrt(np.mean((am - pm) ** 2))),
            "resid_total": float(np.sqrt(np.mean((at - pt) ** 2))),
            "bias_margin": float(np.mean(pm - am))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=120)
    ap.add_argument("--sims", type=int, default=80)
    ap.add_argument("--season", default="2024-25")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--cumulative", action="store_true",
                    help="also remove sources cumulatively (doubles runtime)")
    args = ap.parse_args()

    S = load_sim()
    print(f"Loading {args.season}...", flush=True)
    sim, jobs, dqg = build(S, args.season, args.games, args.seed)
    print(f"games: {len(jobs)}   sims/game: {args.sims}", flush=True)

    # MIN_JITTER_SHRINK, not MIN_JITTER_SD: the empirical-pool path ignores sd
    # entirely, so ablating sd measured nothing and reported it as zero.
    base = dict(PACE_SD=S.PACE_SD, SHOOT_SD_SHARED=S.SHOOT_SD_SHARED,
                SHOOT_SD_TEAM=S.SHOOT_SD_TEAM, USE_SHRINK=S.USE_SHRINK,
                MIN_JITTER_SHRINK=S.MIN_JITTER_SHRINK)

    def setcfg(**kw):
        for k, v in base.items():
            setattr(S, k, kw.get(k, v))

    single = [("full model", {}),
              ("- shared pace draw", {"PACE_SD": 0.0}),
              ("- shared shooting", {"SHOOT_SD_SHARED": 0.0}),
              ("- team shooting", {"SHOOT_SD_TEAM": 0.0}),
              ("- usage variation", {"USE_SHRINK": 0.0}),
              ("- minutes jitter", {"MIN_JITTER_SHRINK": 0.0})]
    cum, acc = [], {}
    for lab, kw in single[1:]:
        acc = {**acc, **kw}
        cum.append((lab.replace("- ", "also - "), dict(acc)))

    ref = None
    print(f"\n{'configuration':<26}{'margin sd':>11}{'total sd':>10}"
          f"{'team sd':>9}{'corr':>8}{'var share':>11}")
    print("-" * 75)
    for lab, kw in single:
        setcfg(**kw)
        r = run(sim, jobs, dqg, args.sims, args.seed)
        print("", end="", flush=True)
        if ref is None:
            ref = r
            share = ""
        else:
            share = f"{(1 - r['margin_sd']**2 / ref['margin_sd']**2) * 100:>10.1f}%"
        print(f"  {lab:<24}{r['margin_sd']:>11.2f}{r['total_sd']:>10.2f}"
              f"{r['team_sd']:>9.2f}{r['corr']:>8.3f}{share:>11}")

    if not args.cumulative:
        setcfg(**{k: v for _, kw in single[1:] for k, v in kw.items()})
        cum = [("all injected noise off", {k: v for _, kw in single[1:]
                                           for k, v in kw.items()})]
    print(f"\n{'cumulative removal':<26}{'margin sd':>11}{'total sd':>10}"
          f"{'team sd':>9}{'corr':>8}")
    print("-" * 75)
    for lab, kw in cum:
        setcfg(**kw)
        r = run(sim, jobs, dqg, args.sims, args.seed)
        print(f"  {lab:<24}{r['margin_sd']:>11.2f}{r['total_sd']:>10.2f}"
              f"{r['team_sd']:>9.2f}{r['corr']:>8.3f}")
    floor = r

    setcfg()
    print("\n=== CALIBRATION TARGET ===")
    print(f"  predictive margin sd (what the engine claims) : {ref['margin_sd']:.2f}")
    print(f"  realised margin RMSE (what it actually gets)  : {ref['resid_margin']:.2f}")
    print(f"  ratio                                          : "
          f"{ref['margin_sd']/ref['resid_margin']:.3f}"
          f"   ({'over' if ref['margin_sd'] > ref['resid_margin'] else 'under'}-dispersed)")
    print(f"  predictive total sd  : {ref['total_sd']:.2f}"
          f"     realised total RMSE : {ref['resid_total']:.2f}")
    print(f"  margin bias (pred - actual) : {ref['bias_margin']:+.2f}")
    print(f"\n  binomial floor (all injected noise off): margin sd {floor['margin_sd']:.2f}")
    excess = ref["margin_sd"] ** 2 - ref["resid_margin"] ** 2
    print(f"  excess variance to remove: {excess:.1f} "
          f"({excess / ref['margin_sd']**2 * 100:.0f}% of predictive variance)")
    if floor["margin_sd"] > ref["resid_margin"]:
        print("\n  NOTE: the floor alone already exceeds the realised error, so the")
        print("  excess CANNOT be removed by tuning the injected noise down. The")
        print("  possession process itself is too random — that is a different fix")
        print("  (correlated outcomes within a possession sequence, not less noise).")


if __name__ == "__main__":
    main()
