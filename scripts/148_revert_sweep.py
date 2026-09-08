"""
How strong should mean reversion be? Tuned on one season, confirmed on another.

Script 124's MEAN_REVERT was derived from a variance decomposition: match the
engine's independent team-score variance (141.7) to the real one (126.0). That
target is defensible but it is the UNCONDITIONAL league spread, and a simulator
forecasting a KNOWN matchup should not be that wide. Its predictive distribution
should match its own error, and the held-out run puts that at a margin residual
sd of 13.1 against a predictive sd of 16.05 — still 22% too wide, with 80%
intervals covering 87%.

So the derived value is probably an under-correction. This script asks the
question the decomposition cannot: which strength actually minimises held-out
log-loss and interval miscoverage?

THE OBVIOUS TRAP IS TUNING ON THE TEST SET. Sweeping a constant on 2024-25 and
then reporting 2024-25 numbers would be selection, not validation, and the
improvement would be partly the constant memorising that season's noise. So the
sweep runs on 2023-24 and only the WINNER is carried to 2024-25, which stays
untouched until then.

Common random numbers throughout: every configuration sees identical seeds on
identical games, so differences are the constant and not sampling.

Usage: python scripts/148_revert_sweep.py [--games 200] [--sims 120]
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]

GRID = [0.0, 0.00108, 0.0020, 0.0030, 0.0040, 0.0055]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def score(sim, jobs, dqg, n_sims, seed):
    """Win-probability and score metrics on one configuration."""
    rng = np.random.default_rng(seed)
    p, y, pm, am, sd = [], [], [], [], []
    for j in jobs:
        if dqg:
            sim.defq = j["defq"] or {}
        res, _, _ = sim.simulate(j["sides"]["H"], j["sides"]["A"], j["hid"], j["aid"],
                                 n_sims=n_sims, seed=int(rng.integers(1 << 30)),
                                 anchor=j["anchor"], pace_pair=j["pace"])
        m = res["H"] - res["A"]
        p.append(float((m > 0).mean()))
        y.append(j["home_win"])
        pm.append(float(m.mean()))
        am.append(j["act_margin"])
        sd.append(float(m.std(ddof=1)))
    p = np.clip(np.asarray(p), 1e-4, 1 - 1e-4)
    y = np.asarray(y, dtype=float)
    pm, am = np.asarray(pm), np.asarray(am)
    resid = float(np.sqrt(np.mean((am - pm) ** 2)))
    pred_sd = float(np.mean(sd))
    return {"logloss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
            "brier": float(np.mean((p - y) ** 2)),
            "acc": float(np.mean((p > 0.5) == (y > 0.5))),
            "mae": float(np.mean(np.abs(am - pm))),
            "pred_sd": pred_sd, "resid_sd": resid, "ratio": pred_sd / resid}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--sims", type=int, default=120)
    ap.add_argument("--tune", default="2023-24")
    ap.add_argument("--test", default="2024-25")
    args = ap.parse_args()

    V = load("vb", ROOT / "scripts" / "143_variance_budget.py")
    S = V.load_sim()
    base = S.MEAN_REVERT

    print(f"TUNING on {args.tune} (the test season is not touched yet)", flush=True)
    sim, jobs, dqg = V.build(S, args.tune, args.games, 23)
    print(f"  {len(jobs)} games x {args.sims} sims", flush=True)
    print(f"\n  {'MEAN_REVERT':<13}{'logloss':>9}{'brier':>8}{'acc':>7}"
          f"{'margin MAE':>12}{'pred sd':>9}{'resid sd':>10}{'ratio':>7}")
    rows = {}
    for k in GRID:
        S.MEAN_REVERT = k
        r = score(sim, jobs, dqg, args.sims, 23)
        rows[k] = r
        print(f"  {k:<13.5f}{r['logloss']:>9.4f}{r['brier']:>8.4f}{r['acc']:>7.3f}"
              f"{r['mae']:>12.2f}{r['pred_sd']:>9.2f}{r['resid_sd']:>10.2f}"
              f"{r['ratio']:>7.3f}", flush=True)

    best = min(rows, key=lambda k: rows[k]["logloss"])
    print(f"\n  best log-loss on {args.tune}: MEAN_REVERT = {best:.5f}"
          f"  (derived value was {base:.5f})")
    # the ratio-matching choice is a second, independent criterion
    ratio_pick = min(rows, key=lambda k: abs(rows[k]["ratio"] - 1.0))
    print(f"  closest to a calibrated predictive sd (ratio 1.0): {ratio_pick:.5f}")

    print(f"\nCONFIRMING on {args.test} — held out from the sweep entirely", flush=True)
    sim2, jobs2, dqg2 = V.build(S, args.test, args.games, 11)
    print(f"  {len(jobs2)} games x {args.sims} sims", flush=True)
    print(f"\n  {'config':<24}{'logloss':>9}{'brier':>8}{'acc':>7}"
          f"{'margin MAE':>12}{'pred sd':>9}{'resid sd':>10}{'ratio':>7}")
    for lab, k in (("off", 0.0), (f"derived ({base:.5f})", base),
                   (f"tuned ({best:.5f})", best)):
        S.MEAN_REVERT = k
        r = score(sim2, jobs2, dqg2, args.sims, 11)
        print(f"  {lab:<24}{r['logloss']:>9.4f}{r['brier']:>8.4f}{r['acc']:>7.3f}"
              f"{r['mae']:>12.2f}{r['pred_sd']:>9.2f}{r['resid_sd']:>10.2f}"
              f"{r['ratio']:>7.3f}", flush=True)
    S.MEAN_REVERT = base
    print("\n  If the tuned value does not beat the derived one on the test season,")
    print("  the sweep found this season's noise and the derived value stands.")


if __name__ == "__main__":
    main()
