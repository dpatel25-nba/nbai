"""
Rookie priors from draft slot — the simulator's blindest spot.

`build_rates` projects from prior seasons, so a player with no NBA history gets a
generic replacement profile. That means the first overall pick and an undrafted
two-way simulate identically, and rookies are 10.7% of 2025-26 minutes. The
north-star notes list rookie priors (draft slot) as a needed piece; the draft
numbers arrived with the bio pull.

METHOD. For every player's FIRST season, regress per-36 production on draft slot,
then shrink. Slot is used as log(slot) rather than raw rank, because the gap
between picks 1 and 5 is far larger than between 25 and 30 — talent is not linear
in draft position. Undrafted players are modelled as their own category rather
than as pick 61, which would extrapolate a line past where it was fit.

TWO TRAPS, BOTH FROM THIS PROJECT'S OWN LESSONS.

  SURVIVORSHIP. If the bio sample only contains players who lasted several years,
  late picks who washed out are invisible and their prior is inflated exactly
  where it matters. The sample's composition is reported before any curve is
  trusted — the same failure that made the aging curves (139) unusable.

  IDENTIFIABILITY. "First season" must mean first NBA season, not first season in
  our data. player_seasons starts at 2013-14, so a veteran's 2013-14 would look
  like a rookie year. FROM_YEAR from the bio pull is used to exclude them.

VALIDATION: does the draft-slot prior predict rookie production better than the
flat replacement profile the simulator uses today? If not, the simple default
stays.

Output: data/parquet/rookie_priors.parquet
Usage: python scripts/141_rookie_priors.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
BIO = ROOT / "data" / "parquet" / "player_bio.parquet"
OUT = ROOT / "data" / "parquet" / "rookie_priors.parquet"

# the diagnostic set, plus every column the simulator's rate profile needs so the
# output can replace its flat replacement profile directly
METRICS = ["MPG", "PTS_36", "REB_36", "AST_36", "TS_PCT", "FG3A_36", "TOV_36",
           "FGA_36", "FTA_36", "OREB_36", "DREB_36", "PF_36",
           "FG3_PCT", "FT_PCT", "FG_PCT", "STL_36", "BLK_36"]
MIN_MIN = 200          # a rookie needs some real playing time to be scored
K_SHRINK = 8.0         # phantom players pulling a slot toward the fitted line


def main() -> None:
    ps = pd.read_parquet(PS)
    bio = pd.read_parquet(BIO)
    if "DRAFT_NUMBER" not in bio.columns:
        raise SystemExit("bio data has no draft numbers")

    draft, fromyr = {}, {}
    for r in bio.itertuples():
        pid = int(r.PLAYER_ID)
        d = str(r.DRAFT_NUMBER)
        draft[pid] = 0 if d.lower().startswith("undraft") else (
            int(d) if d.isdigit() else None)
        try:
            fromyr[pid] = int(r.FROM_YEAR)
        except (TypeError, ValueError):
            fromyr[pid] = None

    ps = ps[ps.PLAYER_ID.isin(draft)].copy()
    ps["start_yr"] = ps.SEASON.str[:4].astype(int)
    first = ps.groupby("PLAYER_ID").start_yr.min().to_dict()
    # a true rookie season: our first observation AND the league's first year
    rk = ps[[first[p] == y and fromyr.get(p) == y
             for p, y in zip(ps.PLAYER_ID, ps.start_yr)]].copy()
    rk["slot"] = rk.PLAYER_ID.map(draft)
    rk = rk[rk.slot.notna() & (rk.MIN >= MIN_MIN)].copy()
    rk["slot"] = rk.slot.astype(int)
    print(f"rookie seasons identified: {len(rk):,}  "
          f"({rk.start_yr.min()}-{rk.start_yr.max()})")

    # ---- survivorship check BEFORE trusting anything ----
    careers = ps.groupby("PLAYER_ID").start_yr.max().to_dict()
    rk["career_len"] = [careers[p] - first[p] + 1 for p in rk.PLAYER_ID]
    rk["drafted"] = rk.slot > 0
    print("\nSample composition (a late-pick prior is only honest if washouts are in):")
    for lo, hi, lab in [(1, 10, "picks 1-10"), (11, 30, "picks 11-30"),
                        (31, 60, "picks 31-60"), (0, 0, "undrafted")]:
        s = rk[(rk.slot >= lo) & (rk.slot <= hi)] if lo else rk[rk.slot == 0]
        if len(s):
            print(f"  {lab:<14}{len(s):>5} rookies   median career "
                  f"{s.career_len.median():.0f} seasons   "
                  f"{(s.career_len <= 2).mean()*100:.0f}% lasted <=2")

    # ---- fit production on log(slot), undrafted separately ----
    rows = []
    dr = rk[rk.slot > 0].copy()
    dr["lg"] = np.log(dr.slot)
    und = rk[rk.slot == 0]
    print(f"\n{'metric':<10}{'pick 1':>9}{'pick 15':>9}{'pick 30':>9}"
          f"{'pick 50':>9}{'undrafted':>11}{'R2':>7}")
    for m in METRICS:
        if m not in rk.columns:
            continue
        d = dr.dropna(subset=[m])
        if len(d) < 60:
            continue
        X = np.column_stack([np.ones(len(d)), d.lg])
        w = np.sqrt(d.MIN.to_numpy())
        b, *_ = np.linalg.lstsq(X * w[:, None], d[m].to_numpy() * w, rcond=None)
        pred = X @ b
        r2 = 1 - np.sum(w * (d[m] - pred) ** 2) / np.sum(w * (d[m] - np.average(d[m], weights=w)) ** 2)
        uv = np.average(und[m].dropna(), weights=und.loc[und[m].notna(), "MIN"]) \
            if und[m].notna().any() else np.nan
        vals = {s: b[0] + b[1] * np.log(s) for s in (1, 15, 30, 50)}
        print(f"  {m:<8}" + "".join(f"{vals[s]:>9.2f}" for s in (1, 15, 30, 50))
              + f"{uv:>11.2f}{r2:>7.3f}")
        for s in range(1, 61):
            n = int((dr.slot == s).sum())
            obs = (np.average(dr.loc[dr.slot == s, m].dropna(),
                              weights=dr.loc[(dr.slot == s) & dr[m].notna(), "MIN"])
                   if ((dr.slot == s) & dr[m].notna()).any() else np.nan)
            fit = b[0] + b[1] * np.log(s)
            val = fit if np.isnan(obs) else (n * obs + K_SHRINK * fit) / (n + K_SHRINK)
            rows.append({"metric": m, "slot": s, "value": val, "n": n})
        if not np.isnan(uv):
            rows.append({"metric": m, "slot": 0, "value": uv,
                         "n": int(und[m].notna().sum())})
    pd.DataFrame(rows).to_parquet(OUT, index=False)

    # ---- validation: slot prior vs the flat replacement the sim uses ----
    print("\nVALIDATION — predicting a rookie's own production")
    print(f"  {'metric':<10}{'flat replacement':>18}{'draft-slot prior':>18}{'change':>9}")
    pri = pd.DataFrame(rows)
    for m in METRICS:
        if m not in rk.columns or not (pri.metric == m).any():
            continue
        d = rk.dropna(subset=[m])
        if len(d) < 60:
            continue
        lookup = {int(r.slot): r.value for _, r in pri[pri.metric == m].iterrows()}
        flat = np.average(d[m], weights=d.MIN)      # what the sim does today
        slotv = np.array([lookup.get(int(s), flat) for s in d.slot])
        e0 = np.average(np.abs(d[m] - flat), weights=d.MIN)
        e1 = np.average(np.abs(d[m] - slotv), weights=d.MIN)
        print(f"  {m:<10}{e0:>18.4f}{e1:>18.4f}{(e1/e0-1)*100:>8.1f}%")
    print("\n  (in-sample for the prior — it shows draft slot carries signal, not")
    print("   that this exact curve generalises to a future draft class)")


if __name__ == "__main__":
    main()
