"""
Rotation engine — WHO IS ON THE FLOOR, AND WHEN.

The simulator can't just know a player's total minutes; it needs to know the 5
men on the court at each moment, because that determines usage, who guards whom,
and which lineups accumulate the score. This learns real rotation shape from the
reconstructed stints (script 120) and turns a pre-game minutes projection into a
minute-by-minute occupancy curve.

Three pieces:

 1. OCCUPANCY EXTRACTION. From stints, every (game, team, player) gets a vector
    over 30-second slots of regulation: the fraction of each slot he was on the
    floor. This is ground truth for what rotations actually look like.

 2. TEMPLATES. Occupancy is averaged into curves keyed by (started?, minutes
    bucket). A 34-minute starter and a 14-minute reserve have very different
    shapes — the starter opens the game, sits early in Q2, returns; the reserve
    appears in defined windows. Templates capture that shape without needing to
    model individual coaching decisions.

 3. FITTING + FEASIBILITY. A template is scaled to a player's projected minutes,
    then every team-game is renormalized so expected bodies on the floor is
    exactly 5 in every slot — the constraint that makes a rotation coherent.
    Without it, projections that are individually reasonable are jointly
    impossible (six starters on the court, or four).

Minutes themselves come from the validated production model (props_features:
recent_min3/5/10 + proj_mpg + started_last + the `vacated` family + load3 +
own_missed*), which is the leakage-safe lever your studies established. Whether a
player is ACTIVE is an INPUT here, not a prediction — that is exactly the
injury/lineup-news wall your notes flag, and the simulator takes it as given so
what-if scenarios are first-class.

Output: data/parquet/rotation_templates.parquet + a sample_lineups() helper the
possession simulator imports.

Usage: python scripts/121_rotation_model.py
"""

from __future__ import annotations

import glob
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STINTS = ROOT / "data" / "parquet" / "stints"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
OUT = ROOT / "data" / "parquet" / "rotation_templates.parquet"

SLOT_SEC = 30.0
N_SLOTS = int(2880 / SLOT_SEC)          # 96 slots of regulation
MIN_BUCKETS = [0, 8, 14, 20, 26, 32, 48]  # projected-minute bands


def bucket_of(mpg: float) -> int:
    for i in range(len(MIN_BUCKETS) - 1):
        if MIN_BUCKETS[i] <= mpg < MIN_BUCKETS[i + 1]:
            return i
    return len(MIN_BUCKETS) - 2


def occupancy_from_stints(df: pd.DataFrame):
    """-> {(game, team, player): occupancy vector}, {(game,team): starters set}."""
    occ: dict = defaultdict(lambda: np.zeros(N_SLOTS))
    starters: dict = {}
    for r in df.itertuples():
        # regulation only: OT has no fixed shape and would distort the templates
        s, e = r.START_SEC, min(r.END_SEC, 2880.0)
        if e <= s:
            continue
        lo, hi = int(s // SLOT_SEC), int(min(e, 2879.99) // SLOT_SEC)
        for side, tid, lineup in (("H", r.HOME_TEAM_ID, r.HOME_LINEUP),
                                  ("A", r.AWAY_TEAM_ID, r.AWAY_LINEUP)):
            if r.PERIOD == 1 and s == 0.0:
                starters[(r.GAME_ID, tid)] = set(lineup)
            for slot in range(lo, hi + 1):
                a = max(s, slot * SLOT_SEC)
                b = min(e, (slot + 1) * SLOT_SEC)
                if b > a:
                    frac = (b - a) / SLOT_SEC
                    for pid in lineup:
                        occ[(r.GAME_ID, tid, pid)][slot] += frac
    return occ, starters


def build_templates(occ, starters):
    """Average occupancy curves by (started?, minutes bucket)."""
    acc = defaultdict(lambda: np.zeros(N_SLOTS))
    cnt = defaultdict(int)
    rows = []
    for (gid, tid, pid), v in occ.items():
        mins = v.sum() * SLOT_SEC / 60.0
        if mins < 1.0:
            continue
        st = int(pid in starters.get((gid, tid), set()))
        key = (st, bucket_of(mins))
        acc[key] += v
        cnt[key] += 1
        rows.append({"GAME_ID": gid, "TEAM_ID": tid, "PLAYER_ID": pid,
                     "MIN": mins, "started": st, "bucket": key[1]})
    tmpl = {k: acc[k] / cnt[k] for k in acc}
    return tmpl, cnt, pd.DataFrame(rows)


def scale_template(curve: np.ndarray, target_min: float) -> np.ndarray:
    """Stretch a template so it integrates to target_min, staying a probability."""
    cur = curve.sum() * SLOT_SEC / 60.0
    if cur <= 0:
        return np.full(N_SLOTS, min(1.0, target_min / 48.0))
    out = curve * (target_min / cur)
    # repeated clip+renormalize: keep <=1 while preserving total minutes
    for _ in range(60):
        over = out > 1.0
        if not over.any():
            break
        excess = (out[over] - 1.0).sum()
        out[over] = 1.0
        room = ~over
        if not room.any() or out[room].sum() <= 0:
            break
        out[room] += excess * out[room] / out[room].sum()
    return np.clip(out, 0.0, 1.0)


def enforce_five(curves: dict) -> dict:
    """Renormalize a team's curves so expected bodies on court == 5 every slot."""
    if not curves:
        return curves
    keys = list(curves)
    M = np.vstack([curves[k] for k in keys])
    for _ in range(80):
        col = M.sum(axis=0)
        col[col <= 0] = 1e-9
        M *= (5.0 / col)                      # match the 5-man constraint
        np.clip(M, 0.0, 1.0, out=M)           # nobody exceeds "fully on court"
        if np.abs(M.sum(axis=0) - 5.0).max() < 1e-3:
            break
    return {k: M[i] for i, k in enumerate(keys)}


def project_rotation(players: list[dict], tmpl: dict) -> dict:
    """players: [{PLAYER_ID, minutes, started}] -> {pid: occupancy curve}."""
    curves = {}
    for p in players:
        key = (int(p["started"]), bucket_of(p["minutes"]))
        base = tmpl.get(key)
        if base is None:
            base = tmpl.get((int(p["started"]), 3), np.full(N_SLOTS, 0.5))
        curves[p["PLAYER_ID"]] = scale_template(base.copy(), p["minutes"])
    return enforce_five(curves)


def sample_lineups(curves: dict, rng: np.random.Generator,
                   persistence: float = 0.85) -> np.ndarray:
    """Draw a concrete on-court 5 per slot. `persistence` keeps players on the
    floor in runs rather than resampling independently (real rotations are
    sticky; independent draws would churn the lineup every 30 seconds)."""
    pids = np.array(list(curves))
    P = np.vstack([curves[p] for p in pids])          # players x slots
    out = np.zeros((N_SLOTS, 5), dtype=np.int64)
    prev: set = set()
    for s in range(N_SLOTS):
        w = P[:, s].astype(float).copy()
        if prev:
            stay = np.array([p in prev for p in pids])
            w = w * np.where(stay, 1.0 + persistence, 1.0)
        w = np.clip(w, 1e-9, None)
        w /= w.sum()
        pick = rng.choice(len(pids), size=5, replace=False, p=w)
        chosen = pids[pick]
        out[s] = chosen
        prev = set(chosen.tolist())
    return out


def main() -> None:
    files = sorted(glob.glob(str(STINTS / "*.parquet")))
    if not files:
        raise SystemExit("No stints yet — run scripts/120_build_stints.py first.")
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df = df[df.SEASON_TYPE == "Regular Season"]
    print(f"Stints loaded: {len(df):,} across {df.GAME_ID.nunique():,} games "
          f"({df.SEASON.min()}…{df.SEASON.max()})")

    occ, starters = occupancy_from_stints(df)
    tmpl, cnt, meta = build_templates(occ, starters)
    print(f"Player-games with occupancy: {len(meta):,}")

    rows = []
    for (st, b), curve in sorted(tmpl.items()):
        rows.append({"started": st, "bucket": b,
                     "lo": MIN_BUCKETS[b], "hi": MIN_BUCKETS[b + 1],
                     "n": cnt[(st, b)], "mean_min": curve.sum() * SLOT_SEC / 60.0,
                     "curve": curve.tolist()})
    pd.DataFrame(rows).to_parquet(OUT, index=False)

    print(f"\n{'role':<9}{'minutes band':<15}{'n':>8}{'avg min':>9}   rotation shape (Q1..Q4)")
    for r in rows:
        c = np.array(r["curve"])
        q = [c[i * 24:(i + 1) * 24].mean() for i in range(4)]
        bar = " ".join(f"{x:.2f}" for x in q)
        role = "starter" if r["started"] else "bench"
        print(f"{role:<9}{str(r['lo'])+'-'+str(r['hi'])+' min':<15}{r['n']:>8,}"
              f"{r['mean_min']:>9.1f}   {bar}")

    # --- validation: does template + true minutes reproduce real occupancy? ---
    print("\nValidation — rebuild each team-game's rotation from minutes alone:")
    err, base_err, five_err, n = [], [], [], 0
    for (gid, tid), grp in meta.groupby(["GAME_ID", "TEAM_ID"]):
        players = [{"PLAYER_ID": r.PLAYER_ID, "minutes": r.MIN, "started": r.started}
                   for r in grp.itertuples()]
        curves = project_rotation(players, tmpl)
        for r in grp.itertuples():
            actual = occ[(gid, tid, r.PLAYER_ID)]
            err.append(np.abs(curves[r.PLAYER_ID] - actual).mean())
            base_err.append(np.abs(np.full(N_SLOTS, r.MIN / 48.0) - actual).mean())
        five_err.append(np.abs(np.vstack(list(curves.values())).sum(axis=0) - 5).max())
        n += 1
        if n >= 400:
            break
    print(f"  team-games checked: {n}")
    print(f"  slot-level occupancy MAE : {np.mean(err):.4f}")
    print(f"  flat-minutes baseline MAE: {np.mean(base_err):.4f}  "
          f"({(1 - np.mean(err)/np.mean(base_err))*100:.1f}% better)")
    print(f"  worst 5-on-court violation: {np.max(five_err):.4f} players")
    print(f"\nSaved templates -> {OUT}")


if __name__ == "__main__":
    main()
