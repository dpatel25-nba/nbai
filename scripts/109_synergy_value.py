"""
WAR bake-off, entrant: synergy-based offensive value (play-type efficiency).

We have box OBPM (24). This builds an independent offensive-value metric from the
Synergy play-type data: how many points a player creates ABOVE league average on
his own possessions, given his play-type mix and efficiency.

  OFF_VALUE/game = Σ_playtype  poss_pt · (player_PPP_pt − league_off_PPP_pt)

Then compare it to box OBPM on three honest tests:
  1. agreement — do the two offensive metrics correlate? (face validity)
  2. team-additivity — does minutes-weighted team value track team offensive rating?
  3. year-over-year stability — is it a persistent skill?
  4. out-of-sample: does PRIOR-season value predict current team offense, and does
     synergy add anything over box OBPM? (the real bake-off question)

Usage: python scripts/109_synergy_value.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "data" / "parquet" / "player_synergy.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
WAR = ROOT / "data" / "parquet" / "player_seasons_war.parquet"
TG = ROOT / "data" / "parquet" / "team_games.parquet"

ORDER = [f"{y}-{str(y + 1)[2:]}" for y in range(2013, 2026)]
PREV = {s: ORDER[i - 1] for i, s in enumerate(ORDER) if i}


def main() -> None:
    syn = pd.read_parquet(SYN)
    off = syn[syn.side == "offensive"].copy()
    # league average offensive PPP per (season, play_type), possession-weighted
    lg = (off.groupby(["SEASON", "play_type"])
             .apply(lambda g: np.average(g.ppp.fillna(0), weights=g.poss.fillna(0) + 1e-9),
                    include_groups=False)
             .rename("lg_ppp").reset_index())
    off = off.merge(lg, on=["SEASON", "play_type"])
    off["val"] = off.poss.fillna(0) * (off.ppp.fillna(0) - off.lg_ppp)
    # sum over play types per (player, season) -> points created above avg per game
    val = off.groupby(["PLAYER_ID", "SEASON", "TEAM_ABBREVIATION"]).val.sum().reset_index()

    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "PLAYER", "TEAM", "MPG", "MIN", "GP"])
    war = pd.read_parquet(WAR, columns=["PLAYER_ID", "SEASON", "OBPM", "BPM"])
    m = (val.merge(ps, on=["PLAYER_ID", "SEASON"], how="inner")
            .merge(war, on=["PLAYER_ID", "SEASON"], how="left"))
    m = m[m.MIN >= 500].copy()
    m["off_val"] = m.val   # points above avg per game (efficiency value)

    # (1) agreement with box OBPM
    q = m.dropna(subset=["OBPM"])
    print(f"Synergy offensive value — {len(m):,} player-seasons (≥500 min)\n")
    print(f"(1) corr(synergy off_val, box OBPM) = {np.corrcoef(q.off_val, q.OBPM)[0,1]:+.3f}  "
          "(both measure offense; expect moderate +)")
    # face check
    latest = m[m.SEASON == "2024-25"].nlargest(10, "off_val")
    print("\n  Top-10 synergy offensive value, 2024-25:")
    for r in latest.itertuples():
        print(f"    {r.PLAYER:<24} {r.TEAM:<4} +{r.off_val:.1f} pts/g  (OBPM {r.OBPM:+.1f})")

    # (2) team-additivity: minutes-weighted team off_val vs team offensive rating
    tg = pd.read_parquet(TG); tg = tg[tg.SEASON_TYPE == "Regular Season"]
    ortg = {(tri, s): g.offensiveRating.mean() for (tri, s), g in tg.groupby(["TEAM_TRICODE", "SEASON"])}
    m["w"] = m.MIN
    team = (m.groupby(["TEAM", "SEASON"])
              .apply(lambda g: np.average(g.off_val, weights=g.w), include_groups=False)
              .rename("team_val").reset_index())
    team["ortg"] = [ortg.get((t, s), np.nan) for t, s in zip(team.TEAM, team.SEASON)]
    team = team.dropna()
    print(f"\n(2) team additivity: corr(minutes-weighted team synergy value, team ORtg) "
          f"= {np.corrcoef(team.team_val, team.ortg)[0,1]:+.3f}  ({len(team)} team-seasons)")

    # (3) year-over-year stability
    m["si"] = m.SEASON.map({s: i for i, s in enumerate(ORDER)})
    cur = m[["PLAYER_ID", "si", "off_val"]]; nxt = cur.rename(columns={"si": "si_n", "off_val": "off_val_n"})
    yoy = cur.merge(nxt, on="PLAYER_ID"); yoy = yoy[yoy.si_n == yoy.si + 1]
    print(f"(3) year-over-year stability: r = {np.corrcoef(yoy.off_val, yoy.off_val_n)[0,1]:+.3f}  "
          f"({len(yoy):,} pairs)   [OBPM YoY ~0.6-0.7 for reference]")

    # (4) out-of-sample: prior-season value -> current team offense; does synergy add to box?
    obpm_prev = {(r.PLAYER_ID, r.SEASON): r.OBPM for r in war.itertuples()}
    val_prev = {(r.PLAYER_ID, r.SEASON): r.off_val for r in m.itertuples()}
    rows = []
    for (tri, s), g in m.groupby(["TEAM", "SEASON"]):
        ps_ = PREV.get(s)
        if ps_ is None or (tri, s) not in ortg:
            continue
        wv = [(r.MIN, obpm_prev.get((r.PLAYER_ID, ps_)), val_prev.get((r.PLAYER_ID, ps_)))
              for r in g.itertuples()]
        wv = [(w, o, v) for w, o, v in wv if o is not None and v is not None and not np.isnan(o)]
        if len(wv) < 5:
            continue
        W = sum(w for w, _, _ in wv)
        rows.append({"tri": tri, "season": s, "ortg": ortg[(tri, s)],
                     "box": sum(w * o for w, o, _ in wv) / W,
                     "syn": sum(w * v for w, _, v in wv) / W})
    d = pd.DataFrame(rows)
    # season-demean to remove league-level ORtg drift
    for c in ["ortg", "box", "syn"]:
        d[c + "_dm"] = d[c] - d.groupby("season")[c].transform("mean")
    y = d.ortg_dm.to_numpy()
    def r2(cols):
        A = np.column_stack([np.ones(len(d))] + [d[c].to_numpy() for c in cols])
        b, *_ = np.linalg.lstsq(A, y, rcond=None); pred = A @ b
        return 1 - np.sum((y - pred) ** 2) / np.sum((y - y.mean()) ** 2)
    print(f"\n(4) OOS: predict current team ORtg from PRIOR-season player value ({len(d)} team-seasons):")
    print(f"    box OBPM only        R² = {r2(['box_dm']):.3f}")
    print(f"    synergy value only   R² = {r2(['syn_dm']):.3f}")
    print(f"    box + synergy        R² = {r2(['box_dm','syn_dm']):.3f}   "
          f"(incremental from synergy = {r2(['box_dm','syn_dm']) - r2(['box_dm']):+.3f})")


if __name__ == "__main__":
    main()
