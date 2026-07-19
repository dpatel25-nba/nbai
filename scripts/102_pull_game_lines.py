"""
Pull historical NBA game lines (spread / total / moneyline) — the market to beat.

Source: sportsbookreviewsonline archive (free), one page per season, HTML tables.
Covers 2013-14 … 2022-23 (overlaps our data). Player props are NOT freely
available historically — game lines are the honest, free half.

SBR row format (2 rows per game, Visitor then Home):
  Date Rot VH Team 1st 2nd 3rd 4th Final Open Close ML 2H
The Open/Close columns encode BOTH numbers across the pair: the smaller value is
the point SPREAD (on the favorite), the larger is the game TOTAL.

Saves raw HTML (data/raw/odds/) + parsed data/parquet/game_lines.parquet, joined
to our GAME_IDs by date + teams.

Usage: python scripts/102_pull_game_lines.py
"""

from __future__ import annotations

import re
import ssl
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "odds"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT = ROOT / "data" / "parquet" / "game_lines.parquet"
SEASONS = [f"{y}-{str(y + 1)[2:]}" for y in range(2013, 2023)]   # 2013-14 … 2022-23
URL = "https://www.sportsbookreviewsonline.com/scoresoddsarchives/nba-odds-{}"

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE

# SBR team name -> our tricode
SBR = {
    "Atlanta": "ATL", "Boston": "BOS", "Brooklyn": "BKN", "NewJersey": "BKN",
    "Charlotte": "CHA", "Chicago": "CHI", "Cleveland": "CLE", "Dallas": "DAL",
    "Denver": "DEN", "Detroit": "DET", "GoldenState": "GSW", "Houston": "HOU",
    "Indiana": "IND", "LAClippers": "LAC", "LALakers": "LAL", "Memphis": "MEM",
    "Miami": "MIA", "Milwaukee": "MIL", "Minnesota": "MIN", "NewOrleans": "NOP",
    "NewYork": "NYK", "OklahomaCity": "OKC", "Orlando": "ORL", "Philadelphia": "PHI",
    "Phoenix": "PHX", "Portland": "POR", "Sacramento": "SAC", "SanAntonio": "SAS",
    "Toronto": "TOR", "Utah": "UTA", "Washington": "WAS", "LosAngeles": "LAL",
}


def fetch(season: str) -> str:
    p = RAW / f"{season}.html"
    if p.exists():
        return p.read_text(errors="ignore")
    req = urllib.request.Request(URL.format(season), headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=30, context=CTX).read().decode("utf-8", "ignore")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html)
    time.sleep(1.5)
    return html


def num(x):
    x = re.sub(r"<[^>]+>", "", str(x)).strip()
    if x.lower() in ("pk", "pk'", "", "nl", "&nbsp;"):
        return 0.0 if x.lower().startswith("pk") else None
    try:
        return float(x)
    except ValueError:
        return None


def parse_season(html: str, season: str) -> list[dict]:
    y0 = int(season[:4])
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.I | re.S)
    cells = [[re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.I | re.S)]
             for r in rows]
    games = []
    i = 0
    data = [c for c in cells if len(c) >= 12 and c[0] != "Date"]
    while i + 1 < len(data):
        v, h = data[i], data[i + 1]
        if not (v[2] in ("V", "N") and h[2] in ("H", "N")):
            i += 1; continue
        try:
            mmdd = v[0].zfill(4); mm, dd = int(mmdd[:-2]), int(mmdd[-2:])
        except ValueError:
            i += 2; continue
        year = y0 if mm >= 9 else y0 + 1
        cv, ch = num(v[10]), num(h[10])   # Close column
        av, ah = SBR.get(v[3].replace(" ", "")), SBR.get(h[3].replace(" ", ""))
        if av is None or ah is None:
            i += 2; continue
        ov, oh = num(v[9]), num(h[9])     # Open column
        rec = {"date": pd.Timestamp(year, mm, dd), "away": av, "home": ah,
               "ml_away": num(v[11]), "ml_home": num(h[11]),
               "home_spread": np.nan, "total": np.nan,
               "open_home_spread": np.nan, "open_total": np.nan}
        if cv is not None and ch is not None and (cv > 0 or ch > 0):
            rec["total"] = max(cv, ch)
            rec["home_spread"] = -min(cv, ch) if ch <= cv else min(cv, ch)  # fav = smaller Close
        if ov is not None and oh is not None and (ov > 0 or oh > 0):
            rec["open_total"] = max(ov, oh)
            rec["open_home_spread"] = -min(ov, oh) if oh <= ov else min(ov, oh)
        games.append(rec)
        i += 2
    return games


def main() -> None:
    all_rows = []
    for s in SEASONS:
        try:
            html = fetch(s)
            g = parse_season(html, s)
            for r in g:
                r["SEASON"] = s
            all_rows += g
            print(f"  {s}: {len(g)} games parsed")
        except Exception as e:
            print(f"  {s}: FAIL {type(e).__name__} {str(e)[:80]}")
    lines = pd.DataFrame(all_rows)

    # join to our GAME_IDs by date + home + away
    games = pd.read_parquet(GAMES, columns=["GAME_ID", "SEASON", "SEASON_TYPE",
                                            "GAME_DATE", "HOME_TEAM", "AWAY_TEAM"])
    games["d"] = pd.to_datetime(games.GAME_DATE).dt.normalize()
    lines["d"] = lines.date.dt.normalize()
    m = lines.merge(games, left_on=["d", "home", "away"], right_on=["d", "HOME_TEAM", "AWAY_TEAM"],
                    how="inner")
    m = m[["GAME_ID", "SEASON_y", "SEASON_TYPE", "d", "home", "away",
           "home_spread", "total", "ml_home", "ml_away",
           "open_home_spread", "open_total"]].rename(columns={"SEASON_y": "SEASON"})
    m.to_parquet(OUT, index=False)
    matched = m.GAME_ID.nunique()
    print(f"\ngame_lines.parquet: {len(m):,} games matched to our GAME_IDs "
          f"({matched}/{lines.shape[0]} parsed lines joined)")
    print(f"  spread coverage {m.home_spread.notna().mean()*100:.0f}%, "
          f"total {m.total.notna().mean()*100:.0f}%, ML {m.ml_home.notna().mean()*100:.0f}%")
    print(f"  seasons {m.SEASON.min()}…{m.SEASON.max()}")


if __name__ == "__main__":
    main()
