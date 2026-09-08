"""
Simulate ANY two teams — full play-by-play and a complete box score.

Everything up to here has been able to replay a game that appears on a schedule:
`134_pbp_sim.py --game 0022400061` pulls that night's roster, its projected
minutes, its Mode-1 anchor and its measured pace, all keyed by GAME_ID. A
hypothetical matchup has none of those keys, so three pieces have to be built
from team identity alone.

  ROSTER. Who is on a team right now, and how many minutes each plays. Taken
  from that team's most recent games, recency-weighted, then rescaled to a legal
  240 team-minutes after any inactives are removed. Removing a starter therefore
  redistributes his minutes to the players who actually absorb them rather than
  leaving the team five men short.

  STRENGTH. The engine deliberately takes the LEVEL top-down (script 124's
  anchor) because a bottom-up sum of player rates loses to a team rating — that
  finding is the reason the anchor exists at all. Mode-1 supplies it for real
  games; here it is solved directly from results:

      points_for = mu + OFF(team) - DEF(opponent) + HCA * home

  a ridge least-squares over every team-game in the season. Opponent adjustment
  falls out of the fit, so a team that padded its record against weak defences
  does not inherit their weakness.

  PACE. The average of the two teams' possession rates, which is what the
  engine's own pace model expects.

The simulation itself is unchanged — same possession engine, same rotations,
same calibrated distributions. Only the inputs are constructed differently.

Usage:
  python scripts/154_matchup.py --home BOS --away NYK
  python scripts/154_matchup.py --home DEN --away LAL --out 2544 --lines 30
  python scripts/154_matchup.py --home OKC --away HOU --sims 200   (no pbp, distributions)
"""

from __future__ import annotations

import argparse
import importlib.util
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GAMES = ROOT / "data" / "parquet" / "games.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"

RECENT_GAMES = 20      # how far back to read a rotation
HALFLIFE = 8.0         # games; recency weight on minutes
MIN_SHARE = 0.03       # drop players below this share of team minutes
RIDGE = 12.0           # on the team-rating fit


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def team_ratings(season: str, before=None):
    """points_for = mu + OFF(team) - DEF(opp) + HCA*home, by ridge least squares."""
    g = pd.read_parquet(GAMES)
    g = g[(g.SEASON == season) & (g.SEASON_TYPE == "Regular Season")]
    g = g.dropna(subset=["HOME_PTS", "AWAY_PTS"])
    if before is not None:
        g = g[g.GAME_DATE < before]
    teams = sorted(set(g.HOME_TEAM_ID) | set(g.AWAY_TEAM_ID))
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    rows, y = [], []
    for r in g.itertuples():
        for tid, oid, pts, home in ((r.HOME_TEAM_ID, r.AWAY_TEAM_ID, r.HOME_PTS, 1),
                                    (r.AWAY_TEAM_ID, r.HOME_TEAM_ID, r.AWAY_PTS, 0)):
            v = np.zeros(2 * n + 2)
            v[idx[tid]] = 1.0                 # offence
            v[n + idx[oid]] = -1.0            # opponent defence
            v[2 * n] = 1.0                    # intercept
            v[2 * n + 1] = float(home)        # home advantage
            rows.append(v)
            y.append(float(pts))
    X = np.asarray(rows)
    yv = np.asarray(y)
    P = np.eye(X.shape[1]) * RIDGE
    P[2 * n, 2 * n] = 0.0                     # do not shrink the intercept
    P[2 * n + 1, 2 * n + 1] = 0.0             # or the home edge
    beta = np.linalg.solve(X.T @ X + P, X.T @ yv)
    return ({t: float(beta[idx[t]]) for t in teams},
            {t: float(beta[n + idx[t]]) for t in teams},
            float(beta[2 * n]), float(beta[2 * n + 1]), len(g))


def roster(season: str, tid: int, before=None, n_games=RECENT_GAMES):
    """Recency-weighted minutes for whoever has actually been playing."""
    pg = pd.read_parquet(PG, columns=["GAME_ID", "GAME_DATE", "SEASON", "SEASON_TYPE",
                                      "TEAM_ID", "PLAYER_ID", "MIN", "position"])
    d = pg[(pg.SEASON == season) & (pg.SEASON_TYPE == "Regular Season")
           & (pg.TEAM_ID == tid)]
    if before is not None:
        d = d[d.GAME_DATE < before]
    if d.empty:
        raise SystemExit(f"no games found for team {tid} in {season}")
    dates = sorted(d.GAME_DATE.unique())[-n_games:]
    d = d[d.GAME_DATE.isin(dates)]
    order = {dt: i for i, dt in enumerate(sorted(dates))}
    last = len(dates) - 1
    d = d.assign(w=[0.5 ** ((last - order[dt]) / HALFLIFE) for dt in d.GAME_DATE])
    out = []
    for pid, grp in d.groupby("PLAYER_ID"):
        # a player who missed games should not be averaged as if he played 0;
        # weight by the games he was AVAILABLE for, which is what appears here
        mins = float(np.average(grp.MIN, weights=grp.w))
        started = float(np.average(
            [1.0 if isinstance(p, str) and p.strip() else 0.0 for p in grp.position],
            weights=grp.w))
        out.append({"pid": int(pid), "minutes": mins, "started": int(started > 0.5),
                    "games": len(grp)})
    return out, dates[-1]


def build_sides(season, home_tid, away_tid, out_ids, before=None):
    sides = {}
    for tag, tid in (("H", home_tid), ("A", away_tid)):
        pl, asof = roster(season, tid, before)
        pl = [p for p in pl if p["pid"] not in out_ids]
        tot = sum(p["minutes"] for p in pl)
        pl = [p for p in pl if p["minutes"] >= MIN_SHARE * tot / max(len(pl), 1)]
        tot = sum(p["minutes"] for p in pl) or 1.0
        for p in pl:
            p["minutes"] *= 240.0 / tot
        sides[tag] = sorted(pl, key=lambda x: -x["minutes"])
    return sides


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True, help="team tricode, e.g. BOS")
    ap.add_argument("--away", required=True)
    ap.add_argument("--season", default="")
    ap.add_argument("--out", default="", help="comma-separated PLAYER_IDs to rule out")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--lines", type=int, default=25)
    ap.add_argument("--sims", type=int, default=0,
                    help=">0 runs the possession engine N times for distributions "
                         "instead of rendering one play-by-play")
    args = ap.parse_args()

    S = load("sim124", ROOT / "scripts" / "124_possession_sim.py")
    M = load("pbp134", ROOT / "scripts" / "134_pbp_sim.py")

    g = pd.read_parquet(GAMES)
    season = args.season or sorted(g.SEASON.unique())[-1]
    g = g[g.SEASON == season]
    code = {}
    for r in g.itertuples():
        code[r.HOME_TEAM] = r.HOME_TEAM_ID
        code[r.AWAY_TEAM] = r.AWAY_TEAM_ID
    for c in (args.home, args.away):
        if c not in code:
            raise SystemExit(f"unknown team '{c}'. known: {' '.join(sorted(code))}")
    hid, aid = code[args.home], code[args.away]
    out_ids = {int(x) for x in args.out.split(",") if x.strip()}

    off, dfn, mu0, hca, ngames = team_ratings(season)
    mu_home = mu0 + off[hid] - dfn[aid] + hca
    mu_away = mu0 + off[aid] - dfn[hid]
    print(f"{args.away} @ {args.home}   {season}   "
          f"(ratings from {ngames:,} games, home edge {hca:+.2f})")
    print(f"  {args.home:<4} offence {off[hid]:+6.2f}  defence {dfn[hid]:+6.2f}"
          f"   projected {mu_home:6.1f}")
    print(f"  {args.away:<4} offence {off[aid]:+6.2f}  defence {dfn[aid]:+6.2f}"
          f"   projected {mu_away:6.1f}")

    sides = build_sides(season, hid, aid, out_ids)
    if out_ids:
        print(f"  ruled OUT: {sorted(out_ids)}")

    rates, pos = S.build_rates(season)
    tmpl = pd.read_parquet(ROOT / "data/parquet/rotation_templates.parquet")
    aff = pd.read_parquet(ROOT / "data/parquet/assignment_affinity.parquet") \
        .set_index("dpos")[S.POSITIONS].to_numpy()
    dq = S.defensive_index(season)
    if dq and isinstance(next(iter(dq)), tuple):
        dq = {}
    pace_map, lg_pace = S.team_pace(season)
    w3 = pd.read_parquet(ROOT / "data/parquet/player_seasons_war_v3.parquet")
    w3 = w3[w3.SEASON == season]
    bpm = {int(r.PLAYER_ID): float(r.BPM3) for r in w3.itertuples()}
    sim = S.Simulator(rates, pos, tmpl, aff, dq, pace_map, lg_pace, bpm,
                      S.minutes_ratio_pools(season), S.usage_ratio_pool(season))

    ps = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "PLAYER"])
    def surname(full: str) -> str:
        """Last name, skipping suffixes — a naive split()[-1] renders players
        called `... Jr.` as literally "Jr."."""
        parts = [x for x in str(full).split() if x]
        SUF = {"jr.", "jr", "sr.", "sr", "ii", "iii", "iv", "v"}
        suf = {x.strip(".") for x in SUF}
        while len(parts) > 1 and parts[-1].lower().strip(".") in suf:
            parts.pop()
        return parts[-1] if parts else str(full)

    names = {int(r.PLAYER_ID): surname(r.PLAYER)
             for r in ps[ps.SEASON == season].itertuples()}

    if args.sims > 0:
        res, box, _ = sim.simulate(sides["H"], sides["A"], hid, aid,
                                   n_sims=args.sims, seed=args.seed,
                                   anchor=(mu_home, mu_away))
        h, a = res["H"], res["A"]
        print(f"\n{args.sims} simulations")
        print(f"  {args.home} {h.mean():.1f}   {args.away} {a.mean():.1f}"
              f"   margin {np.mean(h-a):+.1f}   total {np.mean(h+a):.1f}")
        print(f"  P({args.home} wins) = {float((h > a).mean()):.3f}")
        print(f"\n  {'PLAYER':<20}{'team':>5}{'PTS':>7}{'p10':>6}{'p90':>6}")
        rows = [(names.get(p, str(p)), t, v["PTS"]) for t in ("H", "A")
                for p, v in box[t].items()]
        for nm, t, d in sorted(rows, key=lambda x: -x[2].mean())[:16]:
            lbl = args.home if t == "H" else args.away
            print(f"  {nm[:18]:<20}{lbl:>5}{d.mean():>7.1f}"
                  f"{np.percentile(d,10):>6.0f}{np.percentile(d,90):>6.0f}")
        return

    game = M.PbpGame(S, sim, sides, hid, aid, None,
                     np.random.default_rng(args.seed), season=season)
    ev = game.run(names)
    print(f"\nsimulated play-by-play ({len(ev):,} events)")
    print(f"\n{'clock':<12}{'':<4}{'play':<54}{'score':>10}")
    for e in ev[:args.lines]:
        tag = "" if e["team"] is None else (args.home if e["team"] == "H" else args.away)
        print(f"{e['clock']:<12}{tag:<4}{e['text'][:52]:<54}"
              f"{e['A']:>4}-{e['H']:<5}")
    if len(ev) > args.lines:
        print(f"   ... {len(ev)-args.lines:,} more events")
    print(f"\nFINAL  {args.away} {game.score['A']}  —  {args.home} {game.score['H']}")
    qs = sorted(game.qscore)
    prev = {"H": 0, "A": 0}
    line = {"H": [], "A": []}
    for q in qs:
        for t in ("H", "A"):
            line[t].append(game.qscore[q][t] - prev[t])
            prev[t] = game.qscore[q][t]
    print(f"  {args.away:<5}" + "".join(f"{x:>5}" for x in line["A"]))
    print(f"  {args.home:<5}" + "".join(f"{x:>5}" for x in line["H"]))
    M.print_box(game, names, {"A": args.away, "H": args.home})


if __name__ == "__main__":
    main()
