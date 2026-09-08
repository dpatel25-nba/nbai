"""
A parallel evaluation harness — the reason the last several experiments were slow.

Every configuration comparison in this project runs the same loop: build
projections once, then simulate N games M times each. One configuration at 198
games x 120 sims is roughly 9.5 million possession calls in pure Python, and the
loop has been running on ONE of eight cores while comparisons of four or five
configurations ran back to back. A sweep that should take ten minutes has been
taking an hour.

The work is embarrassingly parallel across games: each game's simulation touches
no state that another game's needs. The only shared object is the Simulator
itself, which is read-only once built, so a FORKED pool inherits it by
copy-on-write with no pickling and no rebuild per worker. That matters — the
projection build alone is about three minutes and must not be repeated eight
times.

ONE CORRECTNESS REQUIREMENT, AND IT IS EASY TO GET WRONG. The serial harness
draws each game's seed from a single generator in iteration order, so results
depend on the order games are visited. Under a pool that order is not
guaranteed, which would silently break the common-random-numbers property that
every paired comparison in this project relies on: two configurations must see
IDENTICAL randomness on identical games or the difference between them is
sampling noise. Seeds here are derived from (base_seed, game_index) instead, so
they are a pure function of the game and reproduce regardless of scheduling,
worker count, or completion order.

Usage:
    from importlib import ...          # see scripts/148 for the loading pattern
    rows = evaluate(sim, jobs, defq_by_game, n_sims=120, seed=11, workers=6)

Run directly to benchmark serial against parallel:
    python scripts/149_fast_eval.py [--games 40] [--sims 60]
"""

from __future__ import annotations

import argparse
import importlib.util
import multiprocessing as mp
import os
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]

# module-level handles so forked workers inherit them without pickling
_SIM = None
_JOBS = None
_DQG = None
_NSIMS = 1
_SEED = 0


def game_seed(base: int, idx: int) -> int:
    """Deterministic per-game seed: a function of the GAME, not of visit order.

    Drawing from a shared generator in loop order makes results depend on
    scheduling, which destroys common random numbers under a pool.
    """
    return int(np.random.default_rng([int(base), int(idx)]).integers(1 << 30))


def _one(idx: int) -> dict:
    j = _JOBS[idx]
    if _DQG:
        _SIM.defq = _DQG.get(j["gid"]) or {}
    res, _, _ = _SIM.simulate(j["sides"]["H"], j["sides"]["A"], j["hid"], j["aid"],
                              n_sims=_NSIMS, seed=game_seed(_SEED, idx),
                              anchor=j["anchor"], pace_pair=j["pace"])
    h, a = res["H"], res["A"]
    m = h - a
    return {"gid": j["gid"], "p_home": float((m > 0).mean()),
            "pred_margin": float(m.mean()), "pred_total": float((h + a).mean()),
            "margin_sd": float(m.std(ddof=1)), "total_sd": float((h + a).std(ddof=1)),
            "team_var": float((h.var(ddof=1) + a.var(ddof=1)) / 2.0),
            "corr": float(np.corrcoef(h, a)[0, 1]) if h.std() > 1e-9 and a.std() > 1e-9
            else np.nan,
            "act_margin": j["act_margin"], "act_total": j["act_total"],
            "home_win": j["home_win"]}


def _init(sim, jobs, dqg, n_sims, seed):
    global _SIM, _JOBS, _DQG, _NSIMS, _SEED
    _SIM, _JOBS, _DQG, _NSIMS, _SEED = sim, jobs, dqg, n_sims, seed


def evaluate(sim, jobs, dqg, n_sims=120, seed=11, workers=None) -> list:
    """Simulate every job, in parallel, with order-independent seeds."""
    _init(sim, jobs, dqg, n_sims, seed)
    n = len(jobs)
    workers = workers or max(1, min(os.cpu_count() - 2, n))
    if workers <= 1:
        return [_one(i) for i in range(n)]
    # fork so the built Simulator is inherited rather than pickled; spawn would
    # re-import and rebuild projections in every worker
    ctx = mp.get_context("fork")
    with ctx.Pool(workers) as pool:
        return pool.map(_one, range(n), chunksize=max(1, n // (workers * 4)))


def metrics(rows: list) -> dict:
    """Win-probability and score metrics from per-game rows."""
    p = np.clip(np.array([r["p_home"] for r in rows]), 1e-4, 1 - 1e-4)
    y = np.array([r["home_win"] for r in rows], dtype=float)
    pm = np.array([r["pred_margin"] for r in rows])
    am = np.array([r["act_margin"] for r in rows])
    pt = np.array([r["pred_total"] for r in rows])
    at = np.array([r["act_total"] for r in rows])
    ll = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    return {"logloss": float(ll.mean()), "ll_game": ll,
            "brier": float(np.mean((p - y) ** 2)),
            "brier_game": (p - y) ** 2,
            "acc": float(np.mean((p > 0.5) == (y > 0.5))),
            "margin_mae": float(np.mean(np.abs(am - pm))),
            "margin_ae": np.abs(am - pm),
            "total_mae": float(np.mean(np.abs(at - pt))),
            "pred_sd": float(np.mean([r["margin_sd"] for r in rows])),
            "resid_sd": float(np.sqrt(np.mean((am - pm) ** 2))),
            "team_sd": float(np.sqrt(np.mean([r["team_var"] for r in rows]))),
            "total_sd": float(np.mean([r["total_sd"] for r in rows])),
            "corr": float(np.nanmean([r["corr"] for r in rows]))}


def paired(a: dict, b: dict, name: str) -> tuple:
    """Paired t on per-game differences — the only honest way to rank configs."""
    key = {"logloss": "ll_game", "brier": "brier_game", "margin_mae": "margin_ae"}[name]
    d = b[key] - a[key]
    se = d.std(ddof=1) / np.sqrt(len(d))
    return float(d.mean()), float(se), float(d.mean() / se if se > 0 else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=40)
    ap.add_argument("--sims", type=int, default=60)
    ap.add_argument("--season", default="2024-25")
    args = ap.parse_args()

    spec = importlib.util.spec_from_file_location(
        "vb", ROOT / "scripts" / "143_variance_budget.py")
    V = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(V)
    S = V.load_sim()
    sim, jobs, dqg = V.build(S, args.season, args.games, 11)
    print(f"benchmark: {len(jobs)} games x {args.sims} sims, {os.cpu_count()} cores",
          flush=True)

    t0 = time.time()
    r1 = evaluate(sim, jobs, dqg, args.sims, 11, workers=1)
    t_ser = time.time() - t0

    w = max(1, os.cpu_count() - 2)
    t0 = time.time()
    r2 = evaluate(sim, jobs, dqg, args.sims, 11, workers=w)
    t_par = time.time() - t0

    m1, m2 = metrics(r1), metrics(r2)
    same = all(abs(a["pred_margin"] - b["pred_margin"]) < 1e-9 for a, b in zip(r1, r2))
    print(f"\n  serial   {t_ser:7.1f}s")
    print(f"  parallel {t_par:7.1f}s on {w} workers   speedup {t_ser/max(t_par,1e-9):.2f}x")
    print(f"\n  identical results: {same}   "
          f"(log-loss {m1['logloss']:.6f} vs {m2['logloss']:.6f})")
    if not same:
        raise SystemExit("parallel results differ from serial — seeds are not "
                         "order-independent and paired comparisons would be invalid")
    print("\n  Seeds are a function of (base, game index), so common random numbers")
    print("  survive parallel scheduling and paired comparisons stay valid.")


if __name__ == "__main__":
    main()
