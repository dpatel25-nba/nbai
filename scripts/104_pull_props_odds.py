"""
Pull historical PLAYER PROP lines from the-odds-api — to test our soft-market edge.

PAID data: every request costs credits, so this is deliberate, not a blind blast.
  --test           pull ONE game's props, print exact credit cost + sample, stop.
  --date 2025-01-15  pull all games on one date.
  --season 2024-25 [--market player_points]  pull a whole season (after test OK).

Flow (the-odds-api v4 historical):
  1. historical events near a pre-tip timestamp  -> event ids + commence times
  2. per event, historical odds snapshot just before tip -> the closing prop line
Saves raw JSON to data/raw/props_odds/{game_id}_{market}.json (resumable). Logs
credits from response headers so we always know the running cost.

Key: put it in data/.odds_key (gitignored) or env THE_ODDS_API_KEY.
Usage: python scripts/104_pull_props_odds.py --test
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GAMES = ROOT / "data" / "parquet" / "games.parquet"
RAW = ROOT / "data" / "raw" / "props_odds"
KEYFILE = ROOT / "data" / ".odds_key"
BASE = "https://api.the-odds-api.com/v4/historical/sports/basketball_nba"
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE

FULL_NAME = {  # the-odds-api full name -> our tricode
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "LA Clippers": "LAC", "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP", "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR", "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}


def api_key() -> str:
    if KEYFILE.exists():
        return KEYFILE.read_text().strip()
    k = os.environ.get("THE_ODDS_API_KEY")
    if not k:
        raise SystemExit("No API key. Put it in data/.odds_key or set THE_ODDS_API_KEY.")
    return k


def get(url: str) -> tuple[dict, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "nbai/1.0"})
    r = urllib.request.urlopen(req, timeout=30, context=CTX)
    hdr = {k.lower(): v for k, v in r.headers.items()}
    return json.loads(r.read().decode()), hdr


def iso(ts) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def our_games():
    g = pd.read_parquet(GAMES, columns=["GAME_ID", "SEASON", "SEASON_TYPE",
                                        "GAME_DATE", "HOME_TEAM", "AWAY_TEAM"])
    g = g[(g.SEASON_TYPE == "Regular Season") & (g.SEASON >= "2023-24")].copy()
    g["d"] = pd.to_datetime(g.GAME_DATE)
    return g


def pull_date(key, date, markets, gmap, spent):
    """Pull props for all our games on one date. gmap: (tricode_home,away)->GAME_ID."""
    # snapshot ~ end of that day (post-tip closing lines); the API returns nearest prior
    snap = iso(pd.Timestamp(date) + timedelta(hours=2, minutes=30))  # ~evening ET next-day UTC
    ev_url = f"{BASE}/events?apiKey={key}&date={snap}"
    events, hdr = get(ev_url)
    spent["used"] = hdr.get("x-requests-used", spent.get("used"))
    spent["remaining"] = hdr.get("x-requests-remaining", spent.get("remaining"))
    data = events.get("data", [])
    pulled = 0
    for ev in data:
        h = FULL_NAME.get(ev.get("home_team")); a = FULL_NAME.get(ev.get("away_team"))
        if h is None or a is None:
            continue
        commence = pd.Timestamp(ev["commence_time"].replace("Z", "+00:00"))
        target = (commence - timedelta(hours=8)).date()   # UTC commence -> US game date
        gid, best = None, 2
        for gd, g_id in gmap.get((h, a), []):             # match on date + teams (teams repeat!)
            diff = abs((gd - target).days)
            if diff < best:
                best, gid = diff, g_id
        if gid is None or best > 1:
            continue
        # two snapshots: OPEN (~5h pre-tip, softer) and CLOSE (~10min pre-tip, sharp),
        # so we can measure closing-line edge AND opening-line CLV (where game edge lived)
        snaps = {"open": commence - timedelta(hours=5), "close": commence - timedelta(minutes=10)}
        for market in markets:
            for tag, ts in snaps.items():
                out = RAW / f"{gid}_{market}_{tag}.json"
                if out.exists():
                    continue
                pre = iso(ts.tz_convert("UTC").tz_localize(None))
                q = urllib.parse.urlencode({"apiKey": key, "date": pre, "regions": "us",
                                            "markets": market, "oddsFormat": "american"})
                odds, hdr = get(f"{BASE}/events/{ev['id']}/odds?{q}")
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(odds))
                spent["used"] = hdr.get("x-requests-used", spent.get("used"))
                spent["remaining"] = hdr.get("x-requests-remaining", spent.get("remaining"))
                pulled += 1
                time.sleep(0.4)
    return pulled, len(data)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--date")
    ap.add_argument("--season")
    ap.add_argument("--market", default="player_points")
    args = ap.parse_args()
    key = api_key()
    markets = [args.market]
    g = our_games()
    gmap = defaultdict(list)
    for r in g.itertuples():
        gmap[(r.HOME_TEAM, r.AWAY_TEAM)].append((r.d.date(), r.GAME_ID))
    spent = {"used": "?", "remaining": "?"}

    if args.test:
        d = g[g.SEASON == "2024-25"].d.min()
        print(f"TEST — props for {d.date()} ({markets[0]})")
        n, ev = pull_date(key, d.date(), markets, gmap, spent)
        print(f"  events found: {ev}, props pulled: {n}")
        print(f"  credits used total: {spent['used']}, remaining: {spent['remaining']}")
        f = sorted(RAW.glob(f"*_{markets[0]}_close.json"))
        if f:
            o = json.loads(f[0].read_text()).get("data", {})
            bk = (o.get("bookmakers") or [{}])[0]
            mk = (bk.get("markets") or [{}])[0]
            outs = mk.get("outcomes", [])[:4]
            print(f"  sample ({bk.get('key')}): "
                  + "; ".join(f"{x.get('description')} {x.get('name')} {x.get('point')}@{x.get('price')}"
                              for x in outs))
        return

    dates = ([pd.Timestamp(args.date).date()] if args.date
             else sorted(g[g.SEASON == args.season].d.dt.date.unique()) if args.season else [])
    total = 0
    for i, d in enumerate(dates, 1):
        try:
            n, ev = pull_date(key, d, markets, gmap, spent)
            total += n
            if i % 5 == 0 or i == len(dates):
                print(f"  [{i}/{len(dates)}] {d} pulled={total} | credits left {spent['remaining']}")
        except Exception as e:
            print(f"  {d}: FAIL {type(e).__name__} {str(e)[:100]}")
            time.sleep(3)
    print(f"Done — {total} prop files, credits remaining {spent['remaining']}")


if __name__ == "__main__":
    main()
