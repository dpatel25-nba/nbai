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
WAR3_F = ROOT / "data" / "parquet" / "player_seasons_war_v3.parquet"
SHOT_F = ROOT / "data" / "parquet" / "player_shot_quality.parquet"
DEF_F = ROOT / "data" / "parquet" / "defender_quality.parquet"
CONS_F = ROOT / "data" / "parquet" / "consistency.parquet"
CLUTCH_F = ROOT / "data" / "parquet" / "clutch.parquet"
WOWY_F = ROOT / "data" / "parquet" / "wowy.json"
PROPS_F = ROOT / "data" / "features" / "props_predictions.parquet"
DEFV2_F = ROOT / "data" / "parquet" / "defender_quality_v2.parquet"
SYN_F = ROOT / "data" / "parquet" / "team_synergy.parquet"
OUT = ROOT / "web" / "data.js"
BURN_IN = "2013-14"
LATEST = "2025-26"

PLAY_LABELS = {
    "Transition": "Transition", "Isolation": "Isolation", "PRBallHandler": "P&R Ball-Handler",
    "PRRollman": "P&R Roll Man", "Postup": "Post-Up", "Spotup": "Spot-Up",
    "Handoff": "Hand-Off", "Cut": "Cut", "OffScreen": "Off Screen",
    "OffRebound": "Putbacks", "Misc": "Misc",
}

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


def player_shot_charts(season: str, player_ids: set) -> dict:
    """Per-player shot cells (only for the given players) — for the detail card."""
    pbp = pd.read_parquet(PBP_DIR / f"{season}.parquet",
                          columns=["PLAYER_ID", "IS_FIELD_GOAL", "SHOT_VALUE",
                                   "SHOT_RESULT", "SHOT_X", "SHOT_Y", "SHOT_DISTANCE"])
    f = pbp[(pbp.IS_FIELD_GOAL == 1) & pbp.SHOT_VALUE.isin([2, 3])
            & pbp.SHOT_RESULT.isin(["Made", "Missed"])
            & pbp.SHOT_X.notna() & pbp.SHOT_Y.notna()
            & pbp.SHOT_Y.between(-40, 400) & pbp.SHOT_X.between(-250, 250)].copy()
    f["made"] = (f.SHOT_RESULT == "Made").astype(int)
    f["pts"] = f.made * f.SHOT_VALUE
    f["dband"] = f.SHOT_DISTANCE.round().clip(0, 35)
    lg_pps = f.groupby("dband").pts.mean().to_dict()
    CELL = 30
    f = f[f.PLAYER_ID.isin(player_ids)]
    f["cx"] = (f.SHOT_X / CELL).round().astype(int) * CELL
    f["cy"] = (f.SHOT_Y / CELL).round().astype(int) * CELL
    out = {}
    for pid, d in f.groupby("PLAYER_ID"):
        cells = []
        for (cx, cy), c in d.groupby(["cx", "cy"]):
            if len(c) < 4:
                continue
            re = c.pts.mean() - lg_pps.get(c.dband.median(), c.pts.mean())
            cells.append({"x": int(cx), "y": int(cy), "n": int(len(c)), "re": round(re, 2)})
        if cells:
            out[str(int(pid))] = cells
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
    """Top players by WAR v3 (box + play-type + tracking), with shot-making + defense."""
    war = pd.read_parquet(WAR3_F)                          # v3: WAR3/OBPM3/DBPM3
    war = war[war.SEASON == season]
    v1 = pd.read_parquet(WAR_F)
    v1 = v1[v1.SEASON == season][["PLAYER_ID", "MIN", "PTS_PG", "REB_PG", "AST_PG"]]
    shot = pd.read_parquet(SHOT_F)
    shot = shot[shot.SEASON == season][["PLAYER_ID", "POE_100"]]
    cons = pd.read_parquet(CONS_F)
    cons = cons[cons.SEASON == season][["PLAYER_ID", "floor", "ceiling", "consistency"]]
    clutch = pd.read_parquet(CLUTCH_F)
    clutch = clutch[clutch.SEASON == season][["PLAYER_ID", "cPTS", "clutch_delta"]]
    m = (war.merge(v1, on="PLAYER_ID", how="inner")
            .merge(shot, on="PLAYER_ID", how="left")
            .merge(cons, on="PLAYER_ID", how="left")
            .merge(clutch, on="PLAYER_ID", how="left"))
    m = m[m["MIN"] >= 500].sort_values("WAR3", ascending=False).head(topn)

    def num(v, d=1):
        return round(float(v), d) if pd.notna(v) else None
    out = []
    for i, r in enumerate(m.itertuples(), 1):
        out.append({"rank": i, "id": int(r.PLAYER_ID), "player": r.PLAYER, "team": r.TEAM,
                    "war": num(r.WAR3), "off": num(getattr(r, "OBPM3", None)),
                    "poe": num(getattr(r, "POE_100", None)),
                    "def": num(getattr(r, "DBPM3", None)),
                    "mpg": num(r.MPG), "pts": num(r.PTS_PG),
                    "reb": num(r.REB_PG), "ast": num(r.AST_PG),
                    "floor": num(getattr(r, "floor", None)), "ceil": num(getattr(r, "ceiling", None)),
                    "cpts": num(getattr(r, "cPTS", None), 0), "cd": num(getattr(r, "clutch_delta", None), 2),
                    "c": COLORS.get(r.TEAM, "#888888")})
    return out


def props(season: str = LATEST, topn: int = 60) -> dict:
    """Props engine: per-player projected line vs actual (season avg) + engine accuracy."""
    pp = pd.read_parquet(PROPS_F)
    acc = {}
    for s, a, p in [("pts", "points", "pred_points"), ("reb", "reb", "pred_reb"),
                    ("ast", "ast", "pred_ast"), ("min", "MIN", "pred_min")]:
        t = pp[[a, p]].dropna()
        acc[s] = round(float((t[a] - t[p]).abs().mean()), 2)
    cur = pp[pp.SEASON == season]
    g = cur.groupby("PLAYER_ID").agg(
        gp=("GAME_ID", "size"), pmin=("pred_min", "mean"), amin=("MIN", "mean"),
        ppts=("pred_points", "mean"), apts=("points", "mean"),
        preb=("pred_reb", "mean"), areb=("reb", "mean"),
        past=("pred_ast", "mean"), aast=("ast", "mean")).reset_index()
    war = pd.read_parquet(WAR_F)
    war = war[war.SEASON == season][["PLAYER_ID", "PLAYER", "TEAM"]]
    g = g.merge(war, on="PLAYER_ID", how="left")
    g = g[(g.gp >= 15) & g.PLAYER.notna()].sort_values("ppts", ascending=False).head(topn)
    rows = []
    for i, r in enumerate(g.itertuples(), 1):
        rows.append({"rank": i, "id": int(r.PLAYER_ID), "player": r.PLAYER, "team": r.TEAM,
                     "gp": int(r.gp), "c": COLORS.get(r.TEAM, "#888888"),
                     "pmin": round(r.pmin, 1), "ppts": round(r.ppts, 1), "apts": round(r.apts, 1),
                     "preb": round(r.preb, 1), "areb": round(r.areb, 1),
                     "past": round(r.past, 1), "aast": round(r.aast, 1)})
    return {"acc": acc, "rows": rows}


def defenders(season: str = LATEST, topn: int = 30) -> list[dict]:
    """Perimeter-defense leaderboard (opponent + assignment-adjusted matchup suppression)."""
    d = pd.read_parquet(DEFV2_F)
    d = d[(d.SEASON == season) & (d.pp >= 1500) & (d.MPG >= 20)]
    d = d.sort_values("DEF_RATING", ascending=False).head(topn)
    out = []
    for i, r in enumerate(d.itertuples(), 1):
        out.append({"rank": i, "player": r.PLAYER, "team": r.TEAM,
                    "rating": round(float(r.DEF_RATING), 2), "mpg": round(float(r.MPG), 1),
                    "poss": int(r.pp), "c": COLORS.get(r.TEAM, "#888888")})
    return out


def synergy(season: str = LATEST) -> dict:
    """Team play-type profiles: offensive freq+PPP and defensive PPP allowed, by play type."""
    syn = pd.read_parquet(SYN_F)
    syn = syn[syn.SEASON == season]
    types = [t for t in PLAY_LABELS if t in set(syn.play_type)]
    lg_def = {t: round(float(syn[(syn.side == "defensive") & (syn.play_type == t)].ppp.mean()), 2)
              for t in types}
    teams = {}
    for tri, d in syn.groupby("TEAM_ABBREVIATION"):
        off = d[d.side == "offensive"].set_index("play_type")
        dfn = d[d.side == "defensive"].set_index("play_type")
        teams[tri] = {
            "off": {t: [round(float(off.loc[t, "poss_pct"]), 3), round(float(off.loc[t, "ppp"]), 2)]
                    for t in types if t in off.index},
            "def": {t: round(float(dfn.loc[t, "ppp"]), 2) for t in types if t in dfn.index},
        }
    return {"types": types, "labels": {t: PLAY_LABELS[t] for t in types},
            "league_def": lg_def, "teams": teams}


def main() -> None:
    players = player_ratings()
    data = {"generated": date.today().isoformat(),
            "season": LATEST,
            "model": model_metrics(),
            "teams": team_ratings(),
            "players": players,
            "factors": four_factors(),
            "shots": shot_charts(),
            "pshots": player_shot_charts(LATEST, {p["id"] for p in players}),
            "wowy": {t: sorted(pl, key=lambda p: -abs(p["impact"]))[:9]
                     for t, pl in json.loads(WOWY_F.read_text()).items()},
            "props": props(),
            "defenders": defenders(),
            "synergy": synergy()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.NBAI_DATA = " + json.dumps(data, indent=2) + ";\n")
    print(f"Wrote {OUT} — {len(data['teams'])} teams, "
          f"model acc {data['model']['accuracy']}, generated {data['generated']}")


if __name__ == "__main__":
    main()
