"""
Export website data: read the parquet pipeline + model outputs and write a single
web/data.js that the site loads. Re-run this whenever the model updates and the
website refreshes — no HTML editing.

Writes: web/data.js  ->  window.NBAI_DATA = { generated, model, teams }

Usage: python scripts/export_web.py
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GAME_LOGS = ROOT / "data" / "parquet" / "game_logs.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
ELO = ROOT / "data" / "features" / "elo_predictions.parquet"
PBP_DIR = ROOT / "data" / "parquet" / "pbp"
WAR_F = ROOT / "data" / "parquet" / "player_seasons_war.parquet"
SHOT_F = ROOT / "data" / "parquet" / "player_shot_quality.parquet"
DEF_F = ROOT / "data" / "parquet" / "defender_quality.parquet"
OUT = ROOT / "web" / "data.js"
BURN_IN = "2013-14"
LATEST = "2025-26"

# team primary colors for the roster dots (kept in sync with the site)
COLORS = {
    "ATL": "#E03A3E", "BKN": "#777777", "BOS": "#007A33", "CHA": "#00788C",
    "CHI": "#CE1141", "CLE": "#860038", "DAL": "#00538C", "DEN": "#0E2240",
    "DET": "#C8102E", "GSW": "#1D428A", "HOU": "#CE1141", "IND": "#FDBB30",
    "LAC": "#C8102E", "LAL": "#552583", "MEM": "#5D76A9", "MIA": "#98002E",
    "MIL": "#00471B", "MIN": "#236192", "NOP": "#85714D", "NYK": "#F58426",
    "OKC": "#007AC1", "ORL": "#0077C0", "PHI": "#006BB6", "PHX": "#E56020",
    "POR": "#E03A3E", "SAC": "#5A2D81", "SAS": "#9EA8B0", "TOR": "#CE1141",
    "UTA": "#00471B", "WAS": "#002B5C",
}

FULL_NAME = {
    "ATL": "Atlanta Hawks", "BKN": "Brooklyn Nets", "BOS": "Boston Celtics",
    "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "LA Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans", "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards",
}


def model_metrics() -> dict:
    logs = pd.read_parquet(GAME_LOGS, columns=["GAME_ID", "SEASON"])
    m = {"games": f"{logs.GAME_ID.nunique():,}", "seasons": logs.SEASON.nunique()}
    ev = pd.read_parquet(ELO)
    ev = ev[ev.SEASON != BURN_IN]
    p = ev.P_HOME.clip(1e-6, 1 - 1e-6)
    y = ev.HOME_WIN
    acc = ((p > 0.5).astype(int) == y).mean()
    logloss = -(y * p.apply(math.log) + (1 - y) * (1 - p).apply(math.log)).mean()
    m["accuracy"] = f"{acc * 100:.1f}%"
    m["logloss"] = f"{logloss:.3f}"
    return m


def team_ratings() -> list[dict]:
    games = pd.read_parquet(GAMES)
    elo = pd.read_parquet(ELO)
    latest_season = sorted(games.SEASON.unique())[-1]

    m = elo.merge(games[["GAME_ID", "HOME_TEAM", "AWAY_TEAM"]], on="GAME_ID")
    home = m[["GAME_DATE", "HOME_TEAM", "ELO_HOME_PRE"]].rename(
        columns={"HOME_TEAM": "abbr", "ELO_HOME_PRE": "elo"})
    away = m[["GAME_DATE", "AWAY_TEAM", "ELO_AWAY_PRE"]].rename(
        columns={"AWAY_TEAM": "abbr", "ELO_AWAY_PRE": "elo"})
    cur = pd.concat([home, away]).sort_values("GAME_DATE").groupby("abbr").tail(1)

    rs = games[(games.SEASON == latest_season) & (games.SEASON_TYPE == "Regular Season")]
    wl: dict[str, list[int]] = {}
    for r in rs.itertuples():
        wl.setdefault(r.HOME_TEAM, [0, 0]); wl.setdefault(r.AWAY_TEAM, [0, 0])
        if r.HOME_WIN:
            wl[r.HOME_TEAM][0] += 1; wl[r.AWAY_TEAM][1] += 1
        else:
            wl[r.HOME_TEAM][1] += 1; wl[r.AWAY_TEAM][0] += 1

    teams = []
    for r in cur.itertuples():
        w, l = wl.get(r.abbr, [0, 0])
        teams.append({"name": FULL_NAME.get(r.abbr, r.abbr), "abbr": r.abbr,
                      "elo": round(float(r.elo)), "w": w, "l": l,
                      "c": COLORS.get(r.abbr, "#888888")})
    teams.sort(key=lambda t: -t["elo"])
    return teams


def shot_charts(season: str = LATEST) -> dict:
    """Per-team shot cells: location, volume, and efficiency vs. league at that range."""
    pbp = pd.read_parquet(PBP_DIR / f"{season}.parquet",
                          columns=["TEAM_TRICODE", "IS_FIELD_GOAL", "SHOT_VALUE",
                                   "SHOT_RESULT", "SHOT_X", "SHOT_Y", "SHOT_DISTANCE"])
    f = pbp[(pbp.IS_FIELD_GOAL == 1) & pbp.SHOT_VALUE.isin([2, 3])
            & pbp.SHOT_RESULT.isin(["Made", "Missed"])
            & pbp.SHOT_X.notna() & pbp.SHOT_Y.notna()
            & pbp.SHOT_Y.between(-40, 400) & pbp.SHOT_X.between(-250, 250)].copy()
    f["made"] = (f.SHOT_RESULT == "Made").astype(int)
    f["pts"] = f.made * f.SHOT_VALUE
    f["dband"] = f.SHOT_DISTANCE.round().clip(0, 35)
    lg_pps = f.groupby("dband").pts.mean().to_dict()
    CELL = 25
    f["cx"] = (f.SHOT_X / CELL).round().astype(int) * CELL
    f["cy"] = (f.SHOT_Y / CELL).round().astype(int) * CELL
    out = {}
    for team, d in f.groupby("TEAM_TRICODE"):
        cells = []
        for (cx, cy), c in d.groupby(["cx", "cy"]):
            if len(c) < 5:
                continue
            re = c.pts.mean() - lg_pps.get(c.dband.median(), c.pts.mean())
            cells.append({"x": int(cx), "y": int(cy), "n": int(len(c)), "re": round(re, 2)})
        out[team] = cells
    return out


def four_factors(season: str = LATEST) -> list[dict]:
    """Offensive & defensive Four Factors per team (eFG%, TOV%, ORB%, FTR)."""
    lg = pd.read_parquet(GAME_LOGS, columns=["GAME_ID", "TEAM_ID", "TEAM_ABBREVIATION",
        "SEASON", "SEASON_TYPE", "FGM", "FGA", "FG3M", "FTA", "OREB", "DREB", "TOV"])
    lg = lg[(lg.SEASON == season) & (lg.SEASON_TYPE == "Regular Season")]
    m = lg.merge(lg, on="GAME_ID", suffixes=("", "_o"))
    m = m[m.TEAM_ID != m.TEAM_ID_o]
    g = m.groupby(["TEAM_ID", "TEAM_ABBREVIATION"]).sum(numeric_only=True).reset_index()
    out = []
    for r in g.itertuples():
        out.append({
            "team": r.TEAM_ABBREVIATION, "c": COLORS.get(r.TEAM_ABBREVIATION, "#888888"),
            "o_efg": round((r.FGM + 0.5 * r.FG3M) / r.FGA * 100, 1),
            "o_tov": round(r.TOV / (r.FGA + 0.44 * r.FTA + r.TOV) * 100, 1),
            "o_orb": round(r.OREB / (r.OREB + r.DREB_o) * 100, 1),
            "o_ftr": round(r.FTA / r.FGA * 100, 1),
            "d_efg": round((r.FGM_o + 0.5 * r.FG3M_o) / r.FGA_o * 100, 1),
            "d_tov": round(r.TOV_o / (r.FGA_o + 0.44 * r.FTA_o + r.TOV_o) * 100, 1),
            "d_orb": round(r.OREB_o / (r.OREB_o + r.DREB) * 100, 1),
            "d_ftr": round(r.FTA_o / r.FGA_o * 100, 1),
        })
    return out


def player_ratings(season: str = LATEST, topn: int = 60) -> list[dict]:
    """Top players for a season by our WAR, with shot-making + defense metrics."""
    war = pd.read_parquet(WAR_F)
    war = war[war.SEASON == season]
    shot = pd.read_parquet(SHOT_F)
    shot = shot[shot.SEASON == season][["PLAYER_ID", "POE_100"]]
    dfd = pd.read_parquet(DEF_F)
    dfd = dfd[dfd.SEASON == season][["PLAYER_ID", "DEF_VAL_100"]]
    m = (war.merge(shot, on="PLAYER_ID", how="left")
            .merge(dfd, on="PLAYER_ID", how="left"))
    m = m[m["MIN"] >= 500].sort_values("WAR", ascending=False).head(topn)

    def num(v, d=1):
        return round(float(v), d) if pd.notna(v) else None
    out = []
    for i, r in enumerate(m.itertuples(), 1):
        out.append({"rank": i, "player": r.PLAYER, "team": r.TEAM,
                    "war": num(r.WAR), "poe": num(getattr(r, "POE_100", None)),
                    "def": num(getattr(r, "DEF_VAL_100", None)),
                    "mpg": num(r.MPG), "pts": num(r.PTS_PG),
                    "reb": num(r.REB_PG), "ast": num(r.AST_PG),
                    "c": COLORS.get(r.TEAM, "#888888")})
    return out


def main() -> None:
    data = {"generated": date.today().isoformat(),
            "season": LATEST,
            "model": model_metrics(),
            "teams": team_ratings(),
            "players": player_ratings(),
            "factors": four_factors(),
            "shots": shot_charts()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.NBAI_DATA = " + json.dumps(data, indent=2) + ";\n")
    print(f"Wrote {OUT} — {len(data['teams'])} teams, "
          f"model acc {data['model']['accuracy']}, generated {data['generated']}")


if __name__ == "__main__":
    main()
