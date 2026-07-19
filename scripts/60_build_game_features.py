"""
Build a leakage-free game-level feature matrix for predictive-power studies.

For every game, snapshot each team's state as it was BEFORE tip-off — rolling
form, efficiency, shooting (Four Factors shooting), pace, rest/schedule, and Elo.
Everything is computed from strictly prior games, so any model trained on it is
honest. Output: data/parquet/game_features.parquet.

Usage: python scripts/60_build_game_features.py
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "data" / "parquet" / "game_logs.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
ELO = ROOT / "data" / "features" / "elo_predictions.parquet"
OUT = ROOT / "data" / "parquet" / "game_features.parquet"
WIN = 10


def main() -> None:
    lg = pd.read_parquet(LOGS, columns=["GAME_ID", "TEAM_ID", "PTS", "FGM", "FGA",
                                        "FG3M", "FTA", "OREB", "TOV"])
    lg["efg"] = (lg.FGM + 0.5 * lg.FG3M) / lg.FGA
    lg["poss"] = lg.FGA + 0.44 * lg.FTA - lg.OREB + lg.TOV
    box = {(r.GAME_ID, r.TEAM_ID): (r.PTS, r.efg, r.poss) for r in lg.itertuples()}

    elo = pd.read_parquet(ELO)
    elo_pre = {}
    gm = pd.read_parquet(GAMES)[["GAME_ID", "HOME_TEAM_ID", "AWAY_TEAM_ID"]]
    em = elo.merge(gm, on="GAME_ID")
    for r in em.itertuples():
        elo_pre[(r.GAME_ID, r.HOME_TEAM_ID)] = r.ELO_HOME_PRE
        elo_pre[(r.GAME_ID, r.AWAY_TEAM_ID)] = r.ELO_AWAY_PRE

    games = pd.read_parquet(GAMES).sort_values(["GAME_DATE", "GAME_ID"])

    hist = defaultdict(lambda: deque(maxlen=WIN))   # per team: dict of recent-game stats
    lastdate = {}
    recent = defaultdict(list)                      # per team: recent game dates

    def snap(tid):
        h = hist[tid]
        if not h:
            return {k: np.nan for k in ("form_margin", "form_win", "pace", "efg", "efg_def", "net")}
        arr = lambda k: np.mean([e[k] for e in h])
        return {"form_margin": arr("margin"), "form_win": arr("win"), "pace": arr("pace"),
                "efg": arr("efg"), "efg_def": arr("efg_def"), "net": arr("net")}

    rows = []
    for g in games.itertuples():
        date = g.GAME_DATE
        rec = {"GAME_ID": g.GAME_ID, "GAME_DATE": date, "SEASON": g.SEASON,
               "SEASON_TYPE": g.SEASON_TYPE, "HOME_WIN": g.HOME_WIN}
        for side, tid in (("H", g.HOME_TEAM_ID), ("A", g.AWAY_TEAM_ID)):
            s = snap(tid)
            for k, v in s.items():
                rec[f"{side}_{k}"] = v
            rest = (date - lastdate[tid]).days if tid in lastdate else 7
            rec[f"{side}_rest"] = min(rest, 7)
            rec[f"{side}_b2b"] = int(rest == 1)
            rec[f"{side}_dens7"] = sum(1 for d in recent[tid] if 0 < (date - d).days <= 7)
            rec[f"{side}_elo"] = elo_pre.get((g.GAME_ID, tid), 1500.0)
        rows.append(rec)

        # update state after the game
        for tid, opp in ((g.HOME_TEAM_ID, g.AWAY_TEAM_ID), (g.AWAY_TEAM_ID, g.HOME_TEAM_ID)):
            pts, efg, poss = box.get((g.GAME_ID, tid), (np.nan, np.nan, np.nan))
            opts, oefg, oposs = box.get((g.GAME_ID, opp), (np.nan, np.nan, np.nan))
            margin = pts - opts
            net = (pts - opts) / poss * 100 if poss else np.nan
            hist[tid].append({"margin": margin, "win": int(margin > 0),
                              "pace": poss, "efg": efg, "efg_def": oefg, "net": net})
            lastdate[tid] = date
            recent[tid] = [d for d in recent[tid] if (date - d).days <= 7] + [date]

    df = pd.DataFrame(rows)
    df.to_parquet(OUT, index=False)
    print(f"Wrote {len(df):,} games x {df.shape[1]} cols to {OUT}")
    print("features:", [c for c in df.columns if c.startswith(("H_", "A_"))])
    print(f"rows with full history (no NaN): {df.dropna().shape[0]:,}")


if __name__ == "__main__":
    main()
