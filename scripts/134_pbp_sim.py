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
PBP_FT_TRIM = 0.066   # see the note at its use site in possession()
# The renderer generates its own possessions from the clock, and the anchor
# assumes a team gets `pace` of them. Adding the penalty branch created a
# possession-ending path that `_pace_scale`'s analytic `cycles` term does not
# model, so the game ran 10% long (113 possessions against a 102.9 target) and
# every anchored score came out 10% high. Correcting the SCALE for that was
# treating the symptom — the fix belongs on the possession count.
PBP_POSS_CAL = 1.0
# Residual after the pace-default fix: +5.5 points per team. The renderer is a
# second implementation of the same game — its own foul, free-throw and rebound
# paths — so its points per possession sits a few percent above the engine's
# closed form, and it still runs 2.7% more possessions than pace on a current
# roster. This applies ONLY on the anchored path (an unanchored render gets a
# flat 1.0), so it cannot disturb the box-score realism measured without it.
PBP_ANCHOR_CAL = 0.955


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
        # An UNKNOWN player must default to the league mean, not to zero. The
        # centring term subtracts the mean for ten average players, so a missing
        # player contributing 0 leaves the sum short by one mean and pushes the
        # adjustment negative — shortening every possession and manufacturing
        # extra ones. It went unnoticed while rosters were derived from played
        # games, because everyone in them had a pace estimate by construction.
        # On a current roster full of rookies and new signings it ran the game
        # 8% fast (110 possessions against a 102 pace).
        self._pace_mu = (float(t.OFF_SEC.mean()), float(t.DEF_SEC.mean()))
        centre = 5.0 * (self._pace_mu[0] + self._pace_mu[1])
        return off, dfn, centre

    def __init__(self, S, sim, sides, home_tid, away_tid, pace_pair, rng,
                 season=None, anchor=None, anchor_ref=None):
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
        self.tfoul = {"H": 0, "A": 0}      # team fouls, reset every period
        # rotation occupancy -> a concrete five per 30s slot
        self.lu = {}
        for t in ("H", "A"):
            pids, M = sim.occupancy(sides[t])
            self.lu[t] = sim._lineups(pids, M, rng)
        self.on = {t: list(self.lu[t][0]) for t in ("H", "A")}
        self.p_off, self.p_def, self.p_centre = self._load_pace_effects()
        self.clock_mult = self._load_clock_profile(season)
        self.team_scale = self._solve_anchor(anchor, anchor_ref)

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

    def _solve_anchor(self, anchor, anchor_ref):
        """Scale each team so the game lands on its projected points.

        The renderer had NO anchor: it played out the rate book and ignored team
        strength entirely, so the projected score shown alongside it was
        decorative. The engine solves this in closed form and the same solve
        works here — calibrate the level against a REFERENCE roster, then apply
        the BPM difference between that reference and who is actually playing.

        The reference is what makes this handle two different problems with one
        mechanism. For an injury it is the healthy roster, so ruling a star out
        costs his impact instead of being rescaled away. Across an offseason it
        is LAST SEASON'S roster, the one the rating was fitted on, so a team that
        traded its second-best player is not still carrying his rating.
        """
        flat = {"H": 1.0, "A": 1.0}
        if anchor is None or not all(np.isfinite(x) for x in anchor):
            return flat
        sim, ref = self.sim, (anchor_ref or {})
        out = {}
        for tag, target in (("H", anchor[0]), ("A", anchor[1])):
            other = "A" if tag == "H" else "H"
            base = ref.get(tag) or self.sides[tag]
            base_opp = ref.get(other) or self.sides[other]
            ppp = sim._team_ppp(base, base_opp)
            if ppp <= 0 or self.pace <= 0:
                out[tag] = 1.0
                continue
            scale = float(np.clip((target / self.pace) / ppp, 0.80, 1.25))
            d_bpm = sim.team_bpm(self.sides[tag]) - sim.team_bpm(base)
            out[tag] = float(np.clip(scale * PBP_ANCHOR_CAL
                                     * (1.0 + (d_bpm / 100.0) / ppp),
                                     0.55, 1.35))
        return out

    def _load_clock_profile(self, season):
        """How long each team lets a possession run, relative to the league.

        Script 150: this is a team property repeating at split-half r = +0.973,
        and 151 turns it into a multiplier. It deliberately does NOT touch any
        scoring rate — the engine already calibrates each team to its observed
        efficiency, so a clock penalty on top would double-count. All this does
        is make the emitted clock look like the team that is playing.

        The PRIOR season is used, not the one being rendered: a forecast cannot
        know how fast a team played in a game it has not seen. Year-over-year
        persistence is +0.575, which is what makes that legitimate.

        The pair is normalised to mean 1.0 so total game length — and therefore
        the pace calibration in _pace_scale — is untouched.
        """
        flat = {"H": 1.0, "A": 1.0}
        f = ROOT / "data" / "parquet" / "team_clock_profile.parquet"
        if not f.exists() or season is None:
            return flat
        prof = pd.read_parquet(f)
        prior = sorted(x for x in prof.SEASON.unique() if x < season)
        if not prior:
            return flat
        cur = prof[prof.SEASON == prior[-1]]
        m = {int(r.TEAM_ID): float(r.mult) for r in cur.itertuples()}
        out = {t: m.get(int(self.tid[t]), 1.0) for t in ("H", "A")}
        avg = (out["H"] + out["A"]) / 2.0
        if avg <= 0:
            return flat
        return {t: out[t] / avg for t in out}

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
        return (target_spp / cycles) / self._observed_mean_dur * PBP_POSS_CAL

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
            self.tfoul = {"H": 0, "A": 0}
            while rem > 0:
                self.sub_check(period, rem, elapsed_total)
                own = self.score[off] - self.score[dfn]
                # who is on the floor changes how long a possession takes:
                # Trae Young and De'Aaron Fox shorten their own, Mitchell Robinson
                # and Bam Adebayo lengthen them, and pests like VanVleet force the
                # opponent to burn clock. Validated at -4.2% held-out (script 136).
                mo, md = self._pace_mu
                padj = (sum(self.p_off.get(int(q), mo) for q in self.on[off])
                        + sum(self.p_def.get(int(q), md) for q in self.on[dfn])
                        - self.p_centre)
                dur = min(max(self._duration(
                    start_kind, scale * self._state_scale(period, rem, own)
                    * self.clock_mult[off]) + padj, 1.0), rem)
                rem -= dur
                elapsed_total += dur
                # floor time, so the box score can report minutes. Both teams
                # are on the court for the same possession.
                for _t in ("H", "A"):
                    for _q in self.on[_t]:
                        self.box[_t][int(_q)]["SEC"] += dur
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
        # The engine trims the base free-throw branch because the penalty branch
        # supplies that share instead. The renderer needs a SMALLER trim than
        # the engine's 0.137: it accumulates team fouls over real periods rather
        # than over an approximated possession index, so it reaches the penalty
        # less often and its penalty branch supplies fewer shots. Measured on
        # this matchup, trim 0.0 gives 1.084 of the league free-throw rate and
        # 0.137 gives 0.909; PBP_FT_TRIM is the value that lands on 1.0.
        _ftw = 0.44 * r["FTA_36"] * (1.0 - PBP_FT_TRIM)
        q = np.array([r["FG2A_36"], r["FG3A_36"], _ftw, r["TOV_36"]])
        q = q / q.sum()

        opos = sim.pos.get(user, "F")
        aw = np.array([sim.aff[S.POSITIONS.index(sim.pos.get(d, "F"))]
                       [S.POSITIONS.index(opos)] for d in dv])
        defender = int(dv[self.rng.choice(5, p=aw / aw.sum())])
        adj = (1.0 - 0.010 * sim.defq.get(defender, 0.0)) * self.team_scale[off]

        # NON-SHOOTING FOULS AND THE PENALTY, ported from the possession engine.
        # The renderer booked a personal foul only on a shooting foul, so it
        # produced 17.6 fouls per game against a real 37.2 — a ratio of 0.47 —
        # and correspondingly too few free throws (0.92). Once a team passes the
        # limit these fouls also become two shots, which is what puts free
        # throws in the last two minutes of a close game.
        if self.rng.random() < S.NONSHOOT_FOUL:
            fw = np.array([rates[d]["PF_36"] + 1e-6 for d in dv])
            fl = int(dv[self.rng.choice(5, p=fw / fw.sum())])
            self.box[dfn][fl]["PF"] += 1
            self.tfoul[dfn] += 1
            if self.tfoul[dfn] > S.PENALTY_LIMIT:
                made = sum(1 for _ in range(2)
                           if self.rng.random() < float(np.clip(r["FT_PCT"], .3, .99)))
                self.box[off][user]["FTA"] += 2
                self.box[off][user]["FTM"] += made
                self.box[off][user]["PTS"] += made
                self.score[off] += made
                self.log(period, rem, dfn, f"{self.name(fl)} Foul (penalty) — "
                                           f"{self.name(user)} {made}/2 FT")
                return "make" if made else "rebound"
            self.log(period, rem, dfn, f"{self.name(fl)} Foul")

        k = self.rng.choice(4, p=q)
        if k == 3:
            live = self.rng.random() < S.LG["live_to_share"]
            self.box[off][user]["TOV"] += 1
            if self.rng.random() < S.LG["stl_share"]:
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
            self.box[off][user]["FTM"] += made
            self.box[dfn][defender]["PF"] += 1
            self.tfoul[dfn] += 1
            self.score[off] += made
            self.log(period, rem, dfn, f"{self.name(defender)} Foul — "
                                       f"{self.name(user)} {made}/{nft} FT")
            return "make" if made else "rebound"

        three = (k == 1)
        pct = r["FG3_PCT"] if three else r["FG2_PCT"]
        made = self.rng.random() < float(np.clip(pct * adj, .05, .95))
        self.box[off][user]["FGA"] += 1
        if three:
            self.box[off][user]["FG3A"] += 1
        val = 3 if three else 2
        label = "3PT Jump Shot" if three else "Layup" if self.rng.random() < .45 else "Jump Shot"
        if made:
            self.box[off][user]["FGM"] += 1
            if three:
                self.box[off][user]["FG3M"] += 1
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
        if (not three) and (not made) and self.rng.random() < S.LG["blk_share"]:
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
        # A share of misses go out of bounds or are booked deadball and are
        # credited to NO player. The possession engine models this (LG["team_reb"])
        # and the renderer did not, which inflated individual rebounds by ~7% —
        # 102 in a game against a real league average near 88.
        team_reb = self.rng.random() < S.LG["team_reb"]
        if self.rng.random() < p_o:
            if not team_reb:
                w = np.array([rates[p]["OREB_36"] + 1e-6 for p in on])
                g = int(on[self.rng.choice(5, p=w / w.sum())])
                self.box[off][g]["REB"] += 1
                self.box[off][g]["OREB"] += 1
                self.log(period, rem, off, f"{self.name(g)} REBOUND (Off)")
            else:
                self.log(period, rem, off, "TEAM REBOUND (Off)")
            return "rebound_off"
        if not team_reb:
            w = np.array([rates[p]["DREB_36"] + 1e-6 for p in dv])
            g = int(dv[self.rng.choice(5, p=w / w.sum())])
            self.box[dfn][g]["REB"] += 1
            self.box[dfn][g]["DREB"] += 1
            self.log(period, rem, dfn, f"{self.name(g)} REBOUND (Def)")
        else:
            self.log(period, rem, dfn, "TEAM REBOUND (Def)")
        return "rebound"


def print_box(game, names, labels):
    """A full box score in the shape a reader expects from a game page."""
    hdr = (f"{'PLAYER':<22}{'MIN':>5}{'FG':>8}{'3PT':>8}{'FT':>8}"
           f"{'OR':>4}{'DR':>4}{'REB':>5}{'AST':>5}{'STL':>4}{'BLK':>4}"
           f"{'TO':>4}{'PF':>4}{'PTS':>5}")
    for t in ("A", "H"):
        rows = sorted(game.box[t].items(), key=lambda kv: -kv[1]["SEC"])
        print(f"\n{labels[t]}")
        print("  " + hdr)
        print("  " + "-" * len(hdr))
        tot = {}
        for pid, v in rows:
            if v["SEC"] < 1 and v["PTS"] == 0 and v["FGA"] == 0:
                continue
            for k in ("FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB", "DREB",
                      "REB", "AST", "STL", "BLK", "TOV", "PF", "PTS", "SEC"):
                tot[k] = tot.get(k, 0.0) + v[k]
            mm = int(v["SEC"] // 60)
            print(f"  {names.get(pid, str(pid))[:20]:<22}{mm:>5}"
                  f"{int(v['FGM']):>4}-{int(v['FGA']):<3}"
                  f"{int(v['FG3M']):>4}-{int(v['FG3A']):<3}"
                  f"{int(v['FTM']):>4}-{int(v['FTA']):<3}"
                  f"{int(v['OREB']):>4}{int(v['DREB']):>4}{int(v['REB']):>5}"
                  f"{int(v['AST']):>5}{int(v['STL']):>4}{int(v['BLK']):>4}"
                  f"{int(v['TOV']):>4}{int(v['PF']):>4}{int(v['PTS']):>5}")
        if tot:
            print("  " + "-" * len(hdr))
            print(f"  {'TOTALS':<22}{int(tot['SEC']//60):>5}"
                  f"{int(tot['FGM']):>4}-{int(tot['FGA']):<3}"
                  f"{int(tot['FG3M']):>4}-{int(tot['FG3A']):<3}"
                  f"{int(tot['FTM']):>4}-{int(tot['FTA']):<3}"
                  f"{int(tot['OREB']):>4}{int(tot['DREB']):>4}{int(tot['REB']):>5}"
                  f"{int(tot['AST']):>5}{int(tot['STL']):>4}{int(tot['BLK']):>4}"
                  f"{int(tot['TOV']):>4}{int(tot['PF']):>4}{int(tot['PTS']):>5}")


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
                   pp if pp[0] else None, np.random.default_rng(args.seed),
                   season=g.SEASON)
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

    print_box(game, names, {"A": g.AWAY_TEAM, "H": g.HOME_TEAM})


if __name__ == "__main__":
    main()
