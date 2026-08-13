"""
Play-by-play simulator — a clock-driven game that emits a real event log.

The possession engine (124) draws a FIXED number of possessions and indexes them
to rotation slots. There is no clock, so it cannot say when anything happened,
cannot produce quarter scores, and cannot emit a play-by-play. This turns that
around: possessions consume TIME, and the possession count becomes an emergent
property of pace rather than an input.

Possession duration is measured from the pbp, and it depends strongly on how the
possession began:

    after a live-ball turnover    8.9s     (already running)
    after a rebound             10.3s
    after a dead-ball turnover  14.7s
    after the opponent scores   16.3s     (you have to inbound it)

overall mean 12.2s, median 12.0, p10 3s, p90 22s.

Those durations do not tile the game by themselves — 181 possessions x 12.2s is
2,208s against 2,880s available, the difference being free throws, timeouts and
dead-ball time. So the shape is taken from the data and SCALED so the expected
possession count matches the pace model, which is already validated (script 124's
in-season pace blend). Shape from measurement, level from calibration.

PACE OF THE PLAYERS ON THE FLOOR. Lineup pace genuinely varies (stint pace has sd
28 poss/48 at the stint level, and players range 95.1 to 111.2 across a season).
But the naive per-player number is CONFOUNDED WITH TEAM — the five fastest are
three Memphis players plus two centres on fast teams, which is one team effect
wearing five hats. Separating a player's own tempo contribution needs a ridge
regression on lineups, the same machinery as RAPM. Until that exists this uses
team pace with a lineup adjustment shrunk hard toward the team, and says so
rather than pretending the per-player figure is clean.

Output: a timestamped event log plus the box score for a single game.

Usage:
  python scripts/134_pbp_sim.py --game 0022500104
  python scripts/134_pbp_sim.py --game 0022500104 --out 203999 --quarters
"""

from __future__ import annotations

import argparse
import importlib.util
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

# measured mean seconds by how the possession started
DUR_BY_START = {"live_to": 8.9, "rebound": 10.3, "dead_to": 14.7, "make": 16.3,
                "rebound_off": 4.0}   # a putback goes up almost immediately
DUR_CV = 0.56          # sd/mean of possession length, from the observed spread
PERIOD_LEN = 720.0
OT_LEN = 300.0
PACE_RAPM = ROOT / "data" / "parquet" / "pace_rapm.parquet"


def load124():
    spec = importlib.util.spec_from_file_location(
        "sim124", ROOT / "scripts" / "124_possession_sim.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def clock_str(period: int, remaining: float) -> str:
    m, s = divmod(max(remaining, 0.0), 60)
    return f"Q{period} {int(m):02d}:{s:04.1f}" if period <= 4 else \
           f"OT{period-4} {int(m):02d}:{s:04.1f}"


class PbpGame:
    """One clock-driven game that records every event as it happens."""

    def _load_pace_effects(self):
        """Per-player possession-duration effects (script 136), centred so an
        average lineup gets no adjustment and the pace calibration is preserved."""
        if not PACE_RAPM.exists():
            return {}, {}, 0.0
        t = pd.read_parquet(PACE_RAPM)
        off = {int(r.PLAYER_ID): float(r.OFF_SEC) for r in t.itertuples()}
        dfn = {int(r.PLAYER_ID): float(r.DEF_SEC) for r in t.itertuples()}
        centre = 5.0 * (float(t.OFF_SEC.mean()) + float(t.DEF_SEC.mean()))
        return off, dfn, centre

    def __init__(self, S, sim, sides, home_tid, away_tid, pace_pair, rng):
        self.S, self.sim, self.sides = S, sim, sides
        self.tid = {"H": home_tid, "A": away_tid}
        self.rng = rng
        self.pace = ((pace_pair[0] + pace_pair[1]) / 2.0 if pace_pair
                     else (sim.pace_map.get(home_tid, sim.lg_pace)
                           + sim.pace_map.get(away_tid, sim.lg_pace)) / 2.0)
        self.events = []
        self.score = {"H": 0, "A": 0}
        self.box = {t: defaultdict(lambda: defaultdict(float)) for t in ("H", "A")}
        self.qscore = defaultdict(lambda: {"H": 0, "A": 0})
        self.true_poss = {"H": 0, "A": 0}
        # rotation occupancy -> a concrete five per 30s slot
        self.lu = {}
        for t in ("H", "A"):
            pids, M = sim.occupancy(sides[t])
            self.lu[t] = sim._lineups(pids, M, rng)
        self.on = {t: list(self.lu[t][0]) for t in ("H", "A")}
        self.p_off, self.p_def, self.p_centre = self._load_pace_effects()

    def _state_scale(self, period, rem, own_margin) -> float:
        """Clock management. Measured against a 12.1s baseline, pace is flat all
        game until the last two minutes, then splits hard: a team trailing by
        3-9 runs 10.6s in the final two minutes and 7.0s inside 24 seconds
        (42% faster), while a team leading by 3-9 milks 13.7s. Before that the
        spread is under a second and not worth modelling."""
        if period < 4 or rem > 120:
            return 1.0
        if rem > 24:
            if -9 <= own_margin <= -3:
                return 0.88
            if 3 <= own_margin <= 9:
                return 1.13
            return 1.0
        if own_margin < 0:
            return 0.58      # chasing: every second counts
        if own_margin > 0:
            return 0.75      # being fouled ends possessions fast anyway
        return 1.0

    # ---- duration ----
    def _duration(self, start_kind: str, scale: float) -> float:
        base = DUR_BY_START.get(start_kind, 12.2) * scale
        # gamma keeps durations positive and right-skewed like the real spread
        shape = 1.0 / (DUR_CV ** 2)
        return float(np.clip(self.rng.gamma(shape, base / shape), 1.0, 30.0))

    def _pace_scale(self) -> float:
        """Seconds-per-possession scale so the expected count matches the pace
        model. Both teams' possessions fill 2880s, so 2*pace possessions must
        consume the game. Calibrated against the mix of possession starts the
        engine actually generates, since a sim that produces more inbounds than
        the league would otherwise run systematically slow."""
        target_spp = 2880.0 / max(self.pace * 2.0, 1e-6)
        # A standard possession does NOT end on an offensive rebound, so one
        # possession spans several shot CYCLES. Each cycle here consumes clock,
        # so the per-cycle budget is the per-possession budget divided by the
        # expected number of cycles, or the game runs long and overscores.
        # Only possessions that END IN A SHOT can be extended by an offensive
        # rebound — turnovers and trips to the line cannot. Ignoring that
        # over-counted cycles and ran the game ~4% fast.
        p_cont = 0.78 * 0.53 * self.S.LG["oreb"]   # P(shot) x P(miss) x P(oreb)
        cycles = 1.0 / max(1.0 - p_cont, 1e-6)
        return (target_spp / cycles) / self._observed_mean_dur

    _observed_mean_dur = 12.2

    def log(self, period, rem, team, text):
        self.events.append({"period": period, "rem": rem, "clock": clock_str(period, rem),
                            "team": team, "text": text,
                            "H": self.score["H"], "A": self.score["A"]})

    def sub_check(self, period, rem, elapsed_total):
        slot = min(int(elapsed_total // 30.0), self.S.N_SLOTS - 1)
        for t in ("H", "A"):
            want = list(self.lu[t][slot])
            cur = self.on[t]
            outs = [p for p in cur if p not in want]
            ins = [p for p in want if p not in cur]
            for o, i in zip(outs, ins):
                self.on[t] = [i if x == o else x for x in self.on[t]]
                self.log(period, rem, t,
                         f"SUB: {self.name(i)} FOR {self.name(o)}")

    def name(self, pid):
        return self.names.get(pid, str(pid))

    def run(self, names):
        self.names = names
        scale = self._pace_scale()
        period, elapsed_total = 1, 0.0
        off, dfn = "A", "H"          # away team gets the opening possession
        start_kind = "rebound"
        while True:
            plen = PERIOD_LEN if period <= 4 else OT_LEN
            rem = plen
            self.log(period, rem, None, f"--- Start of {'Q' if period<=4 else 'OT'}"
                                        f"{period if period<=4 else period-4} ---")
            while rem > 0:
                self.sub_check(period, rem, elapsed_total)
                own = self.score[off] - self.score[dfn]
                # who is on the floor changes how long a possession takes:
                # Trae Young and De'Aaron Fox shorten their own, Mitchell Robinson
                # and Bam Adebayo lengthen them, and pests like VanVleet force the
                # opponent to burn clock. Validated at -4.2% held-out (script 136).
                padj = (sum(self.p_off.get(int(q), 0.0) for q in self.on[off])
                        + sum(self.p_def.get(int(q), 0.0) for q in self.on[dfn])
                        - self.p_centre)
                dur = min(max(self._duration(
                    start_kind, scale * self._state_scale(period, rem, own)) + padj, 1.0), rem)
                rem -= dur
                elapsed_total += dur
                start_kind = self.possession(off, dfn, period, rem)
                self.qscore[period]["H"] = self.score["H"]
                self.qscore[period]["A"] = self.score["A"]
                # an OFFENSIVE rebound keeps the ball; everything else turns it
                # over. Swapping unconditionally handed putbacks to the defence
                # and cost ~15% of the game's possessions.
                if start_kind != "rebound_off":
                    self.true_poss[off] += 1
                    off, dfn = dfn, off
            if period >= 4 and self.score["H"] != self.score["A"]:
                break
            if period >= 8:
                break
            period += 1
        return self.events

    def possession(self, off, dfn, period, rem) -> str:
        """Resolve one possession, emit its events, return how the NEXT one starts."""
        S, sim = self.S, self.sim
        on = np.array(self.on[off])
        dv = np.array(self.on[dfn])
        rates = sim.rates
        uw = np.array([rates[p]["FG2A_36"] + rates[p]["FG3A_36"]
                       + 0.44 * rates[p]["FTA_36"] + rates[p]["TOV_36"] for p in on])
        user = int(on[self.rng.choice(5, p=uw / uw.sum())])
        r = rates[user]
        upp = max(r["FG2A_36"] + r["FG3A_36"] + 0.44 * r["FTA_36"] + r["TOV_36"], 1e-9)
        q = np.array([r["FG2A_36"], r["FG3A_36"], 0.44 * r["FTA_36"], r["TOV_36"]]) / upp

        opos = sim.pos.get(user, "F")
        aw = np.array([sim.aff[S.POSITIONS.index(sim.pos.get(d, "F"))]
                       [S.POSITIONS.index(opos)] for d in dv])
        defender = int(dv[self.rng.choice(5, p=aw / aw.sum())])
        adj = 1.0 - 0.010 * sim.defq.get(defender, 0.0)

        k = self.rng.choice(4, p=q)
        if k == 3:
            live = self.rng.random() < S.LG["live_to_share"]
            self.box[off][user]["TOV"] += 1
            if live and self.rng.random() < S.LG["stl_share"]:
                sw = np.array([rates[d]["STL_36"] + 1e-6 for d in dv])
                th = int(dv[self.rng.choice(5, p=sw / sw.sum())])
                self.box[dfn][th]["STL"] += 1
                self.log(period, rem, off, f"{self.name(user)} Turnover "
                                           f"({self.name(th)} STEAL)")
            else:
                self.log(period, rem, off, f"{self.name(user)} Turnover")
            return "live_to" if live else "dead_to"

        if k == 2:
            nft = 2 + (1 if self.rng.random() < 0.27 else 0)
            made = sum(1 for _ in range(nft)
                       if self.rng.random() < float(np.clip(r["FT_PCT"], .3, .99)))
            self.box[off][user]["PTS"] += made
            self.box[off][user]["FTA"] += nft
            self.box[dfn][defender]["PF"] += 1
            self.score[off] += made
            self.log(period, rem, off, f"{self.name(defender)} Foul — "
                                       f"{self.name(user)} {made}/{nft} FT")
            return "make" if made else "rebound"

        three = (k == 1)
        pct = r["FG3_PCT"] if three else r["FG2_PCT"]
        made = self.rng.random() < float(np.clip(pct * adj, .05, .95))
        self.box[off][user]["FGA"] += 1
        val = 3 if three else 2
        label = "3PT Jump Shot" if three else "Layup" if self.rng.random() < .45 else "Jump Shot"
        if made:
            self.box[off][user]["FGM"] += 1
            self.box[off][user]["PTS"] += val
            self.score[off] += val
            ast_txt = ""
            a_s = sum(rates[p]["AST_36"] for p in on) / sim._ref["AST_36"]
            p_ast = float(np.clip((S.LG["ast3"] if three else S.LG["ast2"]) * a_s, .05, .95))
            if self.rng.random() < p_ast:
                mates = [p for p in on if p != user]
                aw2 = np.array([rates[p]["AST_36"] + 1e-6 for p in mates])
                a = int(mates[self.rng.choice(len(mates), p=aw2 / aw2.sum())])
                self.box[off][a]["AST"] += 1
                ast_txt = f" ({self.name(a)} AST)"
            self.log(period, rem, off,
                     f"{self.name(user)} {label} ({int(self.box[off][user]['PTS'])} PTS){ast_txt}")
            return "make"

        # miss -> rebound
        if not three and self.rng.random() < S.LG["blk_share"]:
            bw = np.array([rates[d]["BLK_36"] + 1e-6 for d in dv])
            b = int(dv[self.rng.choice(5, p=bw / bw.sum())])
            self.box[dfn][b]["BLK"] += 1
            self.log(period, rem, off, f"MISS {self.name(user)} {label} "
                                       f"({self.name(b)} BLOCK)")
        else:
            self.log(period, rem, off, f"MISS {self.name(user)} {label}")
        off_s = sum(rates[p]["OREB_36"] for p in on) / sim._ref["OREB_36"]
        def_s = sum(rates[p]["DREB_36"] for p in dv) / sim._ref["DREB_36"]
        odds = (S.LG["oreb"] / (1 - S.LG["oreb"])) * (off_s / max(def_s, 1e-6))
        p_o = float(np.clip(odds / (1 + odds), .06, .50))
        if self.rng.random() < p_o:
            w = np.array([rates[p]["OREB_36"] + 1e-6 for p in on])
            g = int(on[self.rng.choice(5, p=w / w.sum())])
            self.box[off][g]["REB"] += 1
            self.log(period, rem, off, f"{self.name(g)} REBOUND (Off)")
            return "rebound_off"
        w = np.array([rates[p]["DREB_36"] + 1e-6 for p in dv])
        g = int(dv[self.rng.choice(5, p=w / w.sum())])
        self.box[dfn][g]["REB"] += 1
        self.log(period, rem, dfn, f"{self.name(g)} REBOUND (Def)")
        return "rebound"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--quarters", action="store_true")
    ap.add_argument("--lines", type=int, default=40, help="how many events to print")
    args = ap.parse_args()
    S = load124()
    out_ids = {int(x) for x in args.out.split(",") if x.strip()}

    g, sides = S.load_game_context(args.game, out_ids)
    rates, pos = S.build_rates(g.SEASON)
    tmpl = pd.read_parquet(ROOT / "data/parquet/rotation_templates.parquet")
    aff = pd.read_parquet(ROOT / "data/parquet/assignment_affinity.parquet") \
            .set_index("dpos")[S.POSITIONS].to_numpy()
    dq = S.defensive_index(g.SEASON)
    if dq and isinstance(next(iter(dq)), tuple):
        dq = {p: v for (gg, p), v in dq.items() if gg == args.game}
    pace_map, lg_pace = S.team_pace(g.SEASON)
    sim = S.Simulator(rates, pos, tmpl, aff, dq, pace_map, lg_pace, {},
                      S.minutes_ratio_pools(g.SEASON), S.usage_ratio_pool(g.SEASON))
    for t in sides:
        z = sum(p["minutes"] for p in sides[t])
        if z > 0:
            for p in sides[t]:
                p["minutes"] *= 240.0 / z

    ps = pd.read_parquet(ROOT / "data/parquet/player_seasons.parquet",
                         columns=["PLAYER_ID", "SEASON", "PLAYER"])
    names = {r.PLAYER_ID: r.PLAYER.split()[-1] for r in ps[ps.SEASON == g.SEASON].itertuples()}

    pace_ig = S.team_pace_ingame(g.SEASON)
    pp = (pace_ig.get((args.game, g.HOME_TEAM_ID)), pace_ig.get((args.game, g.AWAY_TEAM_ID)))
    game = PbpGame(S, sim, sides, g.HOME_TEAM_ID, g.AWAY_TEAM_ID,
                   pp if pp[0] else None, np.random.default_rng(args.seed))
    ev = game.run(names)

    print(f"\n{g.AWAY_TEAM} @ {g.HOME_TEAM}  {str(g.GAME_DATE)[:10]}   "
          f"simulated play-by-play ({len(ev):,} events)")
    if out_ids:
        print(f"  ruled OUT: {sorted(out_ids)}")
    print(f"\n{'clock':<12}{'':<4}{'play':<54}{'score':>10}")
    for e in ev[:args.lines]:
        tag = "" if e["team"] is None else (g.HOME_TEAM if e["team"] == "H" else g.AWAY_TEAM)
        print(f"{e['clock']:<12}{tag:<4}{e['text'][:52]:<54}"
              f"{e['A']:>4}-{e['H']:<5}")
    print(f"   ... {max(0, len(ev)-args.lines):,} more events")

    print(f"\nFINAL  {g.AWAY_TEAM} {game.score['A']}  —  {g.HOME_TEAM} {game.score['H']}"
          f"      (actual {g.AWAY_PTS:.0f}-{g.HOME_PTS:.0f})")
    print(f"true possessions: {game.true_poss['A']} / {game.true_poss['H']}"
          f"   (pace target {game.pace:.1f} per team)")

    if args.quarters:
        print("\nBy period:")
        prev = {"H": 0, "A": 0}
        for p in sorted(game.qscore):
            h, a = game.qscore[p]["H"], game.qscore[p]["A"]
            print(f"  {'Q'+str(p) if p<=4 else 'OT'+str(p-4):<5}"
                  f"{g.AWAY_TEAM} {a-prev['A']:>3}   {g.HOME_TEAM} {h-prev['H']:>3}")
            prev = {"H": h, "A": a}

    print("\nTop scorers:")
    for t, lbl in (("A", g.AWAY_TEAM), ("H", g.HOME_TEAM)):
        top = sorted(game.box[t].items(), key=lambda kv: -kv[1]["PTS"])[:5]
        line = "  ".join(f"{names.get(p, p)} {int(v['PTS'])}" for p, v in top)
        print(f"  {lbl}: {line}")


if __name__ == "__main__":
    main()
