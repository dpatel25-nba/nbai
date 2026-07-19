"""
Travel & schedule fatigue features — free scoring signal for the totals edge.

We only ever tested rest/b2b. Richer fatigue is free from the schedule + arena
coordinates: distance flown since last game, timezone shift (jetlag), venue
altitude (Denver/Utah tax visitors), schedule density (games in last 7 days),
road-trip length. Tired teams score & defend worse — and the OPENING total may
not price a specific fatigue spot.

Tests: (1) does combined fatigue predict act_total − open_total (the residual we
bet)? (2) does betting unders on high-fatigue games beat the opening total?
(3) does adding fatigue to sim_mode1's total improve the opening-line edge?

Usage: python scripts/106_travel_fatigue.py
"""

from __future__ import annotations

from collections import defaultdict
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GAMES = ROOT / "data" / "parquet" / "games.parquet"
LINES = ROOT / "data" / "parquet" / "game_lines.parquet"
SIM = ROOT / "data" / "features" / "sim_mode1_predictions.parquet"
BE = 52.38

# arena: (lat, lon, altitude_ft, tz_index west-positive: ET0 CT1 MT2 PT3)
ARENA = {
    "ATL": (33.757, -84.396, 1050, 0), "BOS": (42.366, -71.062, 20, 0),
    "BKN": (40.683, -73.975, 30, 0), "CHA": (35.225, -80.839, 750, 0),
    "CHI": (41.881, -87.674, 600, 1), "CLE": (41.496, -81.688, 650, 0),
    "DAL": (32.790, -96.810, 430, 1), "DEN": (39.749, -105.008, 5280, 2),
    "DET": (42.341, -83.055, 600, 0), "GSW": (37.768, -122.388, 10, 3),
    "HOU": (29.751, -95.362, 50, 1), "IND": (39.764, -86.155, 715, 0),
    "LAC": (34.043, -118.267, 300, 3), "LAL": (34.043, -118.267, 300, 3),
    "MEM": (35.138, -90.051, 260, 1), "MIA": (25.781, -80.187, 7, 0),
    "MIL": (43.045, -87.917, 600, 1), "MIN": (44.979, -93.276, 830, 1),
    "NOP": (29.949, -90.082, 3, 1), "NYK": (40.751, -73.993, 40, 0),
    "OKC": (35.463, -97.515, 1200, 1), "ORL": (28.539, -81.384, 100, 0),
    "PHI": (39.901, -75.172, 40, 0), "PHX": (33.446, -112.071, 1080, 2),
    "POR": (45.532, -122.667, 50, 3), "SAC": (38.580, -121.500, 30, 3),
    "SAS": (29.427, -98.437, 650, 1), "TOR": (43.643, -79.379, 250, 0),
    "UTA": (40.768, -111.901, 4230, 2), "WAS": (38.898, -77.021, 30, 0),
}


def haversine(a, b):
    la1, lo1 = radians(a[0]), radians(a[1]); la2, lo2 = radians(b[0]), radians(b[1])
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 3959 * 2 * asin(sqrt(h))


def main() -> None:
    g = pd.read_parquet(GAMES)
    g = g[g.SEASON_TYPE == "Regular Season"].sort_values("GAME_DATE")
    # per team, track last venue + recent game dates
    last_venue, last_date = {}, {}
    hist = defaultdict(list)   # team -> [dates]
    feat = {}                  # (game, team) -> dict
    for r in g.itertuples():
        venue = r.HOME_TEAM  # game is played at home team's arena
        for team, is_home in [(r.HOME_TEAM, True), (r.AWAY_TEAM, False)]:
            here = ARENA[venue]
            prev = last_venue.get(team)
            dist = haversine(ARENA[prev], here) if prev else 0.0
            tz_shift = abs(here[3] - ARENA[prev][3]) if prev else 0
            rest = (r.GAME_DATE - last_date[team]).days if team in last_date else 3
            g7 = sum(1 for d in hist[team] if 0 < (r.GAME_DATE - d).days <= 7)
            feat[(r.GAME_ID, team)] = {
                "rest": min(rest, 6), "b2b": int(rest == 1), "dist": dist,
                "tz": tz_shift, "alt": here[2] if not is_home else 0,  # altitude taxes visitor
                "games7": g7, "far_b2b": int(rest == 1 and dist > 500)}
            last_venue[team] = venue; last_date[team] = r.GAME_DATE
            hist[team].append(r.GAME_DATE)

    rows = []
    for r in g.itertuples():
        fh, fa = feat[(r.GAME_ID, r.HOME_TEAM)], feat[(r.GAME_ID, r.AWAY_TEAM)]
        rows.append({"GAME_ID": r.GAME_ID, "SEASON": r.SEASON,
                     "h_rest": fh["rest"], "a_rest": fa["rest"],
                     "h_dist": fh["dist"], "a_dist": fa["dist"],
                     "a_tz": fa["tz"], "alt": fa["alt"],
                     "h_g7": fh["games7"], "a_g7": fa["games7"],
                     "a_far_b2b": fa["far_b2b"], "h_far_b2b": fh["far_b2b"],
                     # combined fatigue index: away travel/jetlag/altitude + both density + b2b
                     "fatigue": (fa["dist"] / 1000 + fa["tz"] + fa["alt"] / 2000
                                 + fh["games7"] + fa["games7"] + 2 * fa["b2b"] + fh["b2b"])})
    d = pd.DataFrame(rows)

    lines = pd.read_parquet(LINES)
    sim = pd.read_parquet(SIM)[["GAME_ID", "pred_total", "act_total"]]
    e = d.merge(lines, on="GAME_ID").merge(sim, on="GAME_ID")
    e = e[e.open_total.notna() & (e.act_total != e.open_total)].copy()
    e["resid"] = e.act_total - e.open_total   # what we'd bet: >0 over, <0 under
    print(f"Travel/fatigue vs totals — {len(e):,} games with opening lines\n")

    # (1) does fatigue correlate with the total going under the opening line?
    print("Correlation of fatigue features with (act_total − open_total):")
    for c in ["fatigue", "a_dist", "a_tz", "alt", "a_far_b2b", "h_g7", "a_g7"]:
        r = np.corrcoef(e[c], e.resid)[0, 1]
        print(f"  {c:<12} {r:+.4f}")

    # (2) bet unders on the most-fatigued games (fatigue → fewer points)
    print("\nBet UNDER the opening total on high-fatigue games:")
    for lbl, qq in [("all games (bet under)", 0.0), ("top 40% fatigue", 0.6),
                    ("top 20% fatigue", 0.8), ("top 10% fatigue", 0.9)]:
        m = e.fatigue >= e.fatigue.quantile(qq)
        wr = (e.resid[m] < 0).mean() * 100     # under hits when act < open
        print(f"  {lbl:<22} {wr:5.2f}%  ({m.sum():,} bets)  {'EDGE' if wr > BE else ''}")

    # (3) add fatigue as a linear adjustment to sim's total, re-test the top-20% edge
    from numpy.linalg import lstsq
    seasons = sorted(e.SEASON_x.unique())
    adj = np.zeros(len(e)); ev = e.reset_index(drop=True)
    for T in seasons[2:]:
        tr = (ev.SEASON_x < T).to_numpy(); te = (ev.SEASON_x == T).to_numpy()
        if tr.sum() < 100 or not te.sum():
            continue
        X = np.column_stack([np.ones(tr.sum()), ev.fatigue[tr], ev.a_dist[tr] / 1000, ev.alt[tr] / 1000])
        b, *_ = lstsq(X, (ev.act_total - ev.pred_total)[tr].to_numpy(), rcond=None)
        Xte = np.column_stack([np.ones(te.sum()), ev.fatigue[te], ev.a_dist[te] / 1000, ev.alt[te] / 1000])
        adj[te] = Xte @ b
    ev["pred_adj"] = ev.pred_total + adj
    for name, col in [("sim only", "pred_total"), ("sim + fatigue adj", "pred_adj")]:
        edge = (ev[col] - ev.open_total).abs()
        win = ((ev[col] > ev.open_total) & (ev.act_total > ev.open_total)) | \
              ((ev[col] < ev.open_total) & (ev.act_total < ev.open_total))
        m = edge >= edge.quantile(0.8)
        print(f"\n  {name:<20} top20% edge {win[m].mean()*100:.2f}%  ({m.sum():,} bets)")


if __name__ == "__main__":
    main()
