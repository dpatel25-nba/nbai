"""
Possession-level game simulator — the capstone.

Monte-Carlo a whole game possession by possession, driving every prior component:

    rotation engine (121)  -> who is on the floor in each 30s slot
    assignment model (122) -> which defender is matched to the ball-handler
    Marcel projections     -> each player's leakage-safe per-36 rate profile
    defender quality       -> shot-make adjustment for the matched defender
    stint-derived pace     -> how many possessions the game contains

Each possession: pick the user among the on-court five (usage-weighted), resolve
the matched defender, then draw an outcome from that player's own shot diet
(3PA / 2PA / free-throw trip / turnover), adjust the make probability for the
defender, and settle rebounds and assists across the players actually on the
floor. Repeat for N simulations to get full distributions — win probability,
margin, total, and every player's stat line with uncertainty.

INACTIVE PLAYERS ARE AN INPUT, NOT A PREDICTION. You pass who is out; the
rotation engine redistributes minutes to whoever remains, and usage follows. This
is deliberate: your notes triply-confirm that pre-game injury news is the one
lever the data does not contain, so the simulator takes it as given rather than
pretending to forecast it. That also makes what-if scenarios first-class — "what
does this game look like without Jokic" is the same code path.

Everything is leakage-safe: rates come from prior seasons (Marcel, recency-
weighted, regressed to mean) so simulating a 2025-26 game never consults 2025-26
outcomes.

Usage:
  python scripts/124_possession_sim.py --game 0022500001 --sims 2000
  python scripts/124_possession_sim.py --game 0022500001 --out 201939,203999
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
LOGS = ROOT / "data" / "parquet" / "game_logs.parquet"
TMPL = ROOT / "data" / "parquet" / "rotation_templates.parquet"
AFF = ROOT / "data" / "parquet" / "assignment_affinity.parquet"
DQ = ROOT / "data" / "parquet" / "defender_quality_v2.parquet"
PROPS = ROOT / "data" / "features" / "props_predictions.parquet"
SHRINK = ROOT / "data" / "parquet" / "shrinkage_constants.parquet"
HUS = ROOT / "data" / "parquet" / "player_hustle.parquet"
ZONES_F = ROOT / "data" / "parquet" / "shot_zones.parquet"
BIO = ROOT / "data" / "parquet" / "player_bio.parquet"

RECENCY = {1: 5.0, 2: 4.0, 3: 3.0}
K = 1000.0
SLOT_SEC, N_SLOTS = 30.0, 96
POSITIONS = ["G", "F", "C"]

# league constants, calibrated from our own data in calibrate()
USE_SHRINK = 0.72     # damping on per-game usage variation
MIN_JITTER_SHRINK = 1.0  # damping on per-player minutes variation (no effect on team spread)
FOUL_OUT = 6          # personal fouls that disqualify
NONSHOOT_FOUL = 0.092 # per-possession chance of a non-shooting foul
FOUL_BENCH = 5        # coaches sit a player on this many fouls until late
GARBAGE_MARGIN = 18   # lead that empties the benches late
# Shooting luck splits into a SHARED game component (both teams shoot well in
# the same loose game) and a TEAM-specific one. Only the team-specific part moves
# the margin; the shared part moves the total. Drawing it all as team-specific
# over-dispersed the margin (sd 17.8 against an actual residual sd of ~14.5),
# which makes win probabilities under-confident.
SHOOT_SD_SHARED = 0.045
SHOOT_SD_TEAM = 0.012
# measured from game logs, 2023-24+. The offensive-rebound rate was hardcoded at
# 0.23 against an actual mean of 0.250, biased low on every possession.
LG = {"oreb": 0.250, "ast3": 0.82, "ast2": 0.50, "ft_per_trip": 1.9,
      "stl_share": 0.566,    # share of turnovers that are steals
      "team_reb": 0.072,     # misses booked as TEAM rebounds, credited to nobody
      "blk_share": 0.235,
      # endgame behaviour, measured from Q4 play-by-play. A trailing team's
      # three-point share climbs from ~40% to 46.5% inside two minutes and 64.8%
      # in the last 24 seconds, and it fouls 3-6x more often to stop the clock.
      # Ignoring this distorts exactly the close games that decide win
      # probability.
      "late3_2min": 1.16, "late3_final": 1.62, "hack_prob": 0.55,
      # TRANSITION. Measured from Q1-Q4 pbp: the first shot after a LIVE-ball
      # turnover is worth 1.213 pts against 1.059 after a made basket (+13.8%),
      # comes 7 seconds sooner, and is far closer to the rim (11.1ft vs 14.0,
      # 37.4% threes vs 44.6%). Defensive rebounds do NOT create transition —
      # they yield 1.026, slightly WORSE than conceding a basket, because after a
      # make the offence gets to set up. Without this the engine cannot express
      # why forcing turnovers is worth more than collecting rebounds.
      "live_to_share": 0.76,   # share of turnovers that are live-ball
      "trans_make": 1.13,      # make-probability multiplier in transition
      "trans_3share": 0.84}    # threes are taken less often on the break    # share of missed TWOS blocked. The measured 0.104
                             # is blocks over ALL missed FGs; applying that to
                             # twos alone produced half the real block count.

RATE_COLS = ["FGA_36", "FG3A_36", "FTA_36", "TOV_36", "OREB_36", "DREB_36",
             "AST_36", "PF_36", "STL_36", "BLK_36", "MPG"]
PCT_COLS = ["FG3_PCT", "FT_PCT"]


def pos_bucket(p) -> str:
    if not isinstance(p, str):
        return "F"
    for k in ("C", "G", "F"):
        if k in p:
            return k
    return "F"


def load_K() -> dict:
    """Per-statistic shrinkage constants validated in script 126.

    A single K=1000 was wrong at both ends: volume and role stats (rebounds,
    3PA, assists) are persistent and were being over-shrunk, while shooting
    percentages are mostly noise and were being under-shrunk. Falls back to the
    old constant for anything unvalidated.
    """
    if not SHRINK.exists():
        return {}
    t = pd.read_parquet(SHRINK)
    k = {r.metric: float(r.K_final) for r in t.itertuples()}
    # stats the simulator derives but script 126 does not measure directly
    k.setdefault("FG2A_36", k.get("FGA_36", K))
    k.setdefault("FG2_PCT", k.get("FG_PCT", K))
    k.setdefault("FTA_36", k.get("FGA_36", K))
    return k


def marcel(df: pd.DataFrame, metric: str, weight="MIN", k=None) -> dict:
    """Leakage-safe projection: recency-weighted prior seasons, regressed to mean."""
    Kv = K if k is None else float(k)
    order = {s: i for i, s in enumerate(sorted(df.SEASON.unique()))}
    inv = {i: s for s, i in order.items()}
    val = {(r.PLAYER_ID, r.SEASON): getattr(r, metric) for r in df.itertuples()}
    wt = {(r.PLAYER_ID, r.SEASON): getattr(r, weight) for r in df.itertuples()}
    out, cache = {}, {}
    for r in df.itertuples():
        ti = order[r.SEASON]
        if ti == 0:
            continue
        if ti not in cache:
            pri = df[df.SEASON.map(order) < ti]
            ok = pri[metric].notna() & pri[weight].notna()
            cache[ti] = (np.average(pri.loc[ok, metric], weights=pri.loc[ok, weight])
                         if ok.any() else np.nan)
        pm = cache[ti]
        num = den = 0.0
        for lag, w in RECENCY.items():
            s = inv.get(ti - lag)
            v, m = val.get((r.PLAYER_ID, s)), wt.get((r.PLAYER_ID, s))
            if v is not None and m is not None and not np.isnan(v):
                num += w * m * v
                den += w * m
        if den and not np.isnan(pm):
            out[(r.PLAYER_ID, r.SEASON)] = (num + Kv * pm) / (den + Kv)
    return out


class RateBook(dict):
    """Rate lookup with a replacement-level fallback.

    ~20% of players in a season (rookies, two-way call-ups) have no prior-season
    history and so no Marcel projection. Dropping them is NOT harmless: their
    minutes get redistributed to everyone else by the 240-minute rescale, which
    inflates every surviving player's line. They average 11% of team minutes and
    up to 77% in an extreme game. So unknown players get a replacement profile
    and keep their minutes instead of vanishing.
    """

    def __init__(self, d, fallback):
        super().__init__(d)
        self.fallback = fallback

    def __missing__(self, key):
        return self.fallback


def build_rates(season: str) -> tuple[dict, dict]:
    """-> {pid: rate dict} projected for `season`, and {pid: position}."""
    ps = pd.read_parquet(PS)
    ps["FG2A_36"] = ps.FGA_36 - ps.FG3A_36
    ps["FG2_PCT"] = np.where(ps.FGA_36 > ps.FG3A_36,
                             (ps.FGM_36 - ps.FG3M_36) / (ps.FGA_36 - ps.FG3A_36).clip(0.1),
                             0.5)
    cols = RATE_COLS + PCT_COLS + ["FG2A_36", "FG2_PCT"]
    kmap = load_K()
    proj = {c: marcel(ps, c, k=kmap.get(c)) for c in cols}
    pos = {r.PLAYER_ID: pos_bucket(r.POS) for r in ps.itertuples()}
    rates = defaultdict(dict)
    for c in cols:
        for (pid, s), v in proj[c].items():
            if s == season:
                rates[pid][c] = v
    full = {p: r for p, r in rates.items() if len(r) == len(cols)}
    # replacement profile = median of every projected rate, with usage damped:
    # a player with no track record is a low-usage bench body, not a median starter
    med = {c: float(np.nanmedian([r[c] for r in full.values()])) for c in cols}
    for c in ("FG2A_36", "FG3A_36", "FTA_36", "AST_36"):
        med[c] *= 0.80
    med["MPG"] = 12.0
    return RateBook(full, med), pos


ZONES2 = ["rim", "paint", "mid"]
ZONES3 = ["corner3", "arc3"]


# Defenders match up by SIZE, not just position. Measured from matchup data, the
# share of an offender's possessions a defender takes, relative to pure
# availability, by their height gap: 4+ inches shorter 0.657, 2-4 shorter 1.134,
# within 2 inches 1.340, 2-4 taller 1.009, 4+ taller 0.621. A clean inverted-U on
# same-size matchups — both mismatches get avoided. Worth -5.6% share MAE on top
# of the G/F/C affinity, which is only a three-level proxy for the same thing.
HEIGHT_LIFT = [(-99, -4, 0.657), (-4, -2, 1.134), (-2, 2, 1.340),
               (2, 4, 1.009), (4, 99, 0.621)]
# Weight adds on top of height, measured the same way and validated the same way:
# share MAE 0.0636 -> 0.0625 and corr 0.587 -> 0.603 out of sample. Smaller than
# height, as expected given the two correlate, but not absorbed by it — mass
# matters separately from length when deciding who can guard whom.
WEIGHT_LIFT = [(-999, -30, 0.798), (-30, -10, 1.056), (-10, 10, 1.166),
               (10, 30, 1.048), (30, 999, 0.798)]


def load_heights() -> tuple[dict, dict]:
    if not BIO.exists():
        return {}, {}
    b = pd.read_parquet(BIO)
    h = {int(r.PLAYER_ID): float(r.HEIGHT_IN) for r in b.itertuples()
         if pd.notna(r.HEIGHT_IN)}
    w = {int(r.PLAYER_ID): float(r.WEIGHT_LB) for r in b.itertuples()
         if pd.notna(r.WEIGHT_LB)}
    return h, w


def _lift(table, gap: float) -> float:
    for lo, hi, v in table:
        if lo <= gap < hi:
            return v
    return 1.0


def height_mult(gap: float) -> float:
    return _lift(HEIGHT_LIFT, gap)


def weight_mult(gap: float) -> float:
    return _lift(WEIGHT_LIFT, gap)


def build_zones(season: str):
    """Marcel-projected shot-zone profile per player, prior seasons only.

    Replaces a single FG2_PCT/FG3_PCT with a mix over five zones. The league
    spread is enormous — rim 1.357 pts/shot against mid-range 0.829 — so a
    rim-runner and a mid-range shooter with equal two-point accuracy previously
    simulated identically. Zones also give defence a way to matter that a make-
    probability multiplier cannot express: pushing shots away from the rim.
    """
    if not ZONES_F.exists():
        return {}, {}
    z = pd.read_parquet(ZONES_F)
    cols = [f"sh_{c}" for c in ZONES2 + ZONES3] + [f"fg_{c}" for c in ZONES2 + ZONES3]
    proj = {c: marcel(z, c, weight="FGA", k=400.0) for c in cols}
    out = defaultdict(dict)
    for c in cols:
        for (pid, sn), v in proj[c].items():
            if sn == season:
                out[pid][c] = v
    full = {p: r for p, r in out.items() if len(r) == len(cols)}
    med = {c: float(np.nanmedian([r[c] for r in full.values()])) for c in cols}
    return full, med


MIN_BINS = (4, 12, 20, 28, 36, 49)


def minutes_ratio_pools(season: str):
    """Empirical actual/predicted minutes ratios, bucketed by projected minutes.

    Minutes error is heteroscedastic and heavily left-tailed, and both matter:
    a 4-12 minute player has ratio sd 0.75 and falls below 60% of projection 37%
    of the time, while a 36+ minute starter has sd 0.12 and does so 1% of the
    time. A single Gaussian jitter is wrong at both ends — far too tight for the
    bench (which is why the 80% intervals only covered 74.5%) and too loose for
    starters. Bootstrapping the real ratios reproduces the skew and the fat lower
    tail exactly, including early exits and foul trouble.

    Built only from seasons BEFORE `season`, so it stays leakage-safe.
    """
    p = pd.read_parquet(PROPS, columns=["SEASON", "pred_min", "MIN"])
    p = p[(p.SEASON < season)].dropna(subset=["pred_min", "MIN"])
    p = p[p.pred_min > MIN_BINS[0]]
    ratio = (p.MIN / p.pred_min).clip(0.0, 3.0).to_numpy()
    pm = p.pred_min.to_numpy()
    pools = {}
    for i in range(len(MIN_BINS) - 1):
        m = (pm >= MIN_BINS[i]) & (pm < MIN_BINS[i + 1])
        pools[i] = ratio[m] if m.sum() > 200 else ratio
    return pools


def usage_ratio_pool(season: str) -> np.ndarray:
    """Per-game usage-rate ratios vs a player's season mean (prior seasons only).

    The engine spread possessions too evenly across the on-court five, so a player
    who was on the floor essentially always got touches. Real role players go
    invisible: usage swings game to game with sd 0.36 and lands below 40% of a
    player's own norm 3.7% of the time. Without that variation the simulator
    produced scoreless games only 6.1% of the time against a real rate of 9.1%,
    and far too rarely for 8-28 minute players (6.3% vs 12.5% at 14-20 min).
    """
    pg = pd.read_parquet(PG, columns=["SEASON", "SEASON_TYPE", "PLAYER_ID", "MIN",
                                      "fieldGoalsAttempted", "freeThrowsAttempted",
                                      "turnovers"])
    pg = pg[(pg.SEASON_TYPE == "Regular Season") & (pg.MIN >= 3) & (pg.SEASON < season)].copy()
    pg["use"] = (pg.fieldGoalsAttempted + 0.44 * pg.freeThrowsAttempted
                 + pg.turnovers) / pg.MIN
    mean = pg.groupby(["PLAYER_ID", "SEASON"]).use.transform("mean")
    r = (pg.use / mean).replace([np.inf, -np.inf], np.nan).dropna()
    return r[(r >= 0) & (r < 4)].to_numpy()


def min_bucket(m: float) -> int:
    for i in range(len(MIN_BINS) - 1):
        if MIN_BINS[i] <= m < MIN_BINS[i + 1]:
            return i
    return 0 if m < MIN_BINS[0] else len(MIN_BINS) - 2


def team_pace_ingame(season: str, k_games: float = 20.0) -> dict:
    """(GAME_ID, TEAM_ID) -> pace, blending prior season with in-season form.

    Pace had exactly the blind spot the player rates did: the simulator used a
    PRIOR-SEASON team average and ignored how fast the team has actually been
    playing this year. Teams change tempo between seasons (new coach, new
    personnel), so the stale number costs real accuracy — an empirical-Bayes
    blend at K=20 games is 3.8% better at predicting a team's possessions.
    Strictly prior-to-tipoff, so it stays leakage-safe.
    """
    order = [f"{y}-{str(y + 1)[2:]}" for y in range(2013, 2027)]
    idx = order.index(season) if season in order else 0
    lg = pd.read_parquet(LOGS, columns=["GAME_ID", "TEAM_ID", "GAME_DATE", "SEASON",
                                        "SEASON_TYPE", "FGA", "FTA", "OREB", "TOV"])
    lg = lg[lg.SEASON_TYPE == "Regular Season"].copy()
    lg["poss"] = lg.FGA + 0.44 * lg.FTA - lg.OREB + lg.TOV
    prior = (lg[lg.SEASON == order[idx - 1]].groupby("TEAM_ID").poss.mean().to_dict()
             if idx else {})
    lg_mean = float(lg[lg.SEASON == season].poss.mean())
    cur = lg[lg.SEASON == season].sort_values("GAME_DATE")
    acc, out = defaultdict(lambda: [0.0, 0]), {}
    for r in cur.itertuples():
        p0 = prior.get(r.TEAM_ID, lg_mean)
        tot, n = acc[r.TEAM_ID]
        std = tot / n if n else p0
        out[(r.GAME_ID, r.TEAM_ID)] = (n * std + k_games * p0) / (n + k_games)
        acc[r.TEAM_ID][0] += r.poss
        acc[r.TEAM_ID][1] += 1
    return out


def defensive_index(season: str, ingame: bool = True) -> dict:
    """Per-player defensive quality from hustle activity.

    Returns either a season-level dict {pid: z} or, with `ingame`, a per-game
    dict {(GAME_ID, pid): z} that blends the prior season with what the player
    has actually done SO FAR this season — the same temporal blindness that was
    costing the player rates and the pace model. Deflection rate is persistent
    (year-over-year r = +0.82), so the prior is a strong starting point, but by
    midseason the current year should dominate.

    Replaces defender_quality_v2, which script 89 showed adds zero portable
    signal and which the metric's own docstring flags as descriptive-only.
    Script 128 found deflections are the strongest available defensive predictor
    -- correlation -0.292 with team defensive rating against -0.256 for rim
    protection and -0.181 for box DBPM, and -4.8% CV RMSE on top of both.

    Prior season only, so simulating a game never consults its own season.
    """
    order = [f"{y}-{str(y + 1)[2:]}" for y in range(2013, 2027)]
    idx = order.index(season) if season in order else 0
    if idx == 0:
        return {}
    prev = order[idx - 1]
    if not HUS.exists():
        return {}
    h = pd.read_parquet(HUS, columns=["SEASON", "SEASON_TYPE", "PLAYER_ID",
                                      "MINUTES", "DEFLECTIONS"])
    h = h[(h.SEASON == prev) & (h.SEASON_TYPE == "Regular Season")].copy()
    if not len(h):
        return {}
    h["mins"] = pd.to_numeric(h.MINUTES.astype(str).str.split(":").str[0],
                              errors="coerce").fillna(0)
    g = h.groupby("PLAYER_ID").agg(mins=("mins", "sum"),
                                   defl=("DEFLECTIONS", "sum")).reset_index()
    g = g[g.mins >= 200]
    if len(g) < 30:
        return {}
    # Empirical-Bayes shrinkage toward the league rate. Raw per-36 rates let
    # small-sample players rank alongside genuine stoppers — Paul Reed and
    # Trevelin Queen were scoring near Thybulle and Caruso purely on ~300
    # minutes of noise. K is the minutes at which a player's own rate and the
    # league mean carry equal weight.
    K_DEFL = 900.0
    lg_rate = g.defl.sum() / g.mins.sum()
    raw = g.defl / g.mins
    g["r"] = ((raw * g.mins + lg_rate * K_DEFL) / (g.mins + K_DEFL)) * 36
    mu, sd = float(g.r.mean()), float(g.r.std(ddof=0) or 1.0)
    prior_rate = {int(p): float(v) for p, v in zip(g.PLAYER_ID, g.r)}
    if not ingame:
        return {p: (v - mu) / sd for p, v in prior_rate.items()}

    cur = pd.read_parquet(HUS, columns=["SEASON", "SEASON_TYPE", "GAME_ID",
                                        "GAME_DATE", "PLAYER_ID", "MINUTES",
                                        "DEFLECTIONS"])
    cur = cur[(cur.SEASON == season) & (cur.SEASON_TYPE == "Regular Season")].copy()
    if not len(cur):
        return {p: (v - mu) / sd for p, v in prior_rate.items()}
    cur["mins"] = pd.to_numeric(cur.MINUTES.astype(str).str.split(":").str[0],
                                errors="coerce").fillna(0)
    cur = cur.sort_values("GAME_DATE")
    acc, out = defaultdict(lambda: [0.0, 0.0]), {}
    for r in cur.itertuples():
        pid = int(r.PLAYER_ID)
        d_td, m_td = acc[pid]
        base = prior_rate.get(pid, mu)
        obs = (d_td / m_td * 36.0) if m_td > 0 else base
        blended = (m_td * obs + K_DEFL * base) / (m_td + K_DEFL)
        out[(r.GAME_ID, pid)] = (blended - mu) / sd
        acc[pid][0] += (r.DEFLECTIONS or 0)
        acc[pid][1] += r.mins
    return out


def team_pace(season: str) -> dict:
    """Prior-season possessions per 48 by team (leakage-safe)."""
    lg = pd.read_parquet(LOGS, columns=["TEAM_ID", "SEASON", "SEASON_TYPE",
                                        "FGA", "FTA", "OREB", "TOV", "MIN"])
    lg = lg[lg.SEASON_TYPE == "Regular Season"].copy()
    lg["poss"] = lg.FGA + 0.44 * lg.FTA - lg.OREB + lg.TOV
    order = sorted(lg.SEASON.unique())
    prev = order[order.index(season) - 1] if season in order and order.index(season) else season
    g = lg[lg.SEASON == prev].groupby("TEAM_ID").poss.mean()
    return g.to_dict(), float(g.mean())


class Simulator:
    def __init__(self, rates, pos, tmpl, aff, defq, pace_map, lg_pace, bpm=None,
                 min_pools=None, use_pool=None, zones=None, zone_fallback=None,
                 heights=None, weights=None):
        self.rates, self.pos, self.aff = rates, pos, aff
        self.defq, self.pace_map, self.lg_pace = defq, pace_map, lg_pace
        # BPM3 (WAR v3) is points per 100 possessions above average — the only
        # component that sees a player's impact BEYOND his own box rates
        # (playmaking gravity, defensive attention). Without it, removing an MVP
        # barely moves the team, because his possessions just flow to teammates
        # at nearly the same per-possession efficiency.
        self.bpm = bpm or {}
        self.min_pools = min_pools
        # reference on-court totals, so lineup strength is measured RELATIVE to a
        # league-average five rather than in raw per-36 units
        vals = list(self.rates.values()) or [getattr(self.rates, "fallback", {})]
        def _mean(c):
            xs = [v.get(c, 0.0) for v in vals if c in v]
            return float(np.mean(xs)) if xs else 1.0
        self._ref = {c: 5.0 * max(_mean(c), 1e-6)
                     for c in ("OREB_36", "DREB_36", "AST_36", "STL_36", "BLK_36")}
        self.use_pool = use_pool
        self.zones = zones or {}
        self.zone_fb = zone_fallback or {}
        self.heights = heights or {}
        self.weights = weights or {}
        self._use_mult = {}
        self.tmpl = {(int(r.started), int(r.bucket)): np.array(r.curve)
                     for r in tmpl.itertuples()}
        self.team_scale = {"H": 1.0, "A": 1.0}

    # ---- rotation ----
    def _bucket(self, m):
        for i, (lo, hi) in enumerate(zip([0, 8, 14, 20, 26, 32], [8, 14, 20, 26, 32, 48])):
            if lo <= m < hi:
                return i
        return 5

    def occupancy(self, players):
        """players: [{pid, minutes, started}] -> occupancy matrix (players x slots)."""
        curves = {}
        for p in players:
            base = self.tmpl.get((int(p["started"]), self._bucket(p["minutes"])))
            if base is None:
                base = np.full(N_SLOTS, p["minutes"] / 48.0)
            c = base.copy()
            cur = c.sum() * SLOT_SEC / 60.0
            c = c * (p["minutes"] / cur) if cur > 0 else c
            for _ in range(50):
                over = c > 1
                if not over.any():
                    break
                ex = (c[over] - 1).sum()
                c[over] = 1.0
                room = ~over
                if not room.any() or c[room].sum() <= 0:
                    break
                c[room] += ex * c[room] / c[room].sum()
            curves[p["pid"]] = np.clip(c, 0, 1)
        pids = list(curves)
        M = np.vstack([curves[p] for p in pids])
        for _ in range(60):                      # exactly five bodies per slot
            col = M.sum(axis=0)
            col[col <= 0] = 1e-9
            M *= 5.0 / col
            np.clip(M, 0, 1, out=M)
            if abs(M.sum(axis=0) - 5).max() < 1e-3:
                break
        return pids, M

    def team_bpm(self, players) -> float:
        """Team net rating implied by a roster, in points per 100 possessions.

        BPM SUMS over the five men on the floor — five average players make a 0
        net rating, not an average of 0 spread across the roster. So the team
        aggregate is sum(BPM x MIN)/48, not the minutes-weighted MEAN. Dividing by
        240 instead under-counts a player's worth by a factor of five, which made
        losing an MVP look like a 0.3-point event.
        """
        return sum(p["minutes"] * self.bpm.get(p["pid"], -1.5)
                   for p in players) / 48.0

    def _team_ppp(self, players, opp=None) -> float:
        """Expected points per possession for a lineup, in closed form.

        Includes the offensive-rebound geometric tail: a possession that misses
        and is rebounded by the offence gets another attempt, so the expectation
        is E1 / (1 - continuation), not E1.
        """
        # The engine now resolves offensive rebounds from the two lineups, so the
        # anchor has to use the SAME rate. Leaving it at the league constant made
        # a strong rebounding team overshoot its calibration target — Denver's
        # projected margin drifted from +8.1 to +13.9 before this was matched up.
        p_oreb = LG["oreb"]
        if opp:
            mw = lambda pl, c: (sum(x["minutes"] * self.rates[x["pid"]][c] for x in pl)
                                / max(sum(x["minutes"] for x in pl), 1e-9)) * 5.0
            off_s = mw(players, "OREB_36") / self._ref["OREB_36"]
            def_s = mw(opp, "DREB_36") / self._ref["DREB_36"]
            odds = (LG["oreb"] / (1 - LG["oreb"])) * (off_s / max(def_s, 1e-6))
            p_oreb = float(np.clip(odds / (1 + odds), 0.06, 0.50))
        # The engine gets a transition premium after the OPPONENT's live-ball
        # turnovers, so the anchor must expect it too — otherwise the level is
        # calibrated against a slower offence than the one actually simulated.
        trans_lift = 1.0
        if opp:
            tw = sum(x["minutes"] for x in opp) or 1e-9
            opp_to = sum(x["minutes"] * (self.rates[x["pid"]]["TOV_36"] /
                         max(self.rates[x["pid"]]["FG2A_36"] + self.rates[x["pid"]]["FG3A_36"]
                             + 0.44 * self.rates[x["pid"]]["FTA_36"]
                             + self.rates[x["pid"]]["TOV_36"], 1e-9))
                         for x in opp) / tw
            p_trans = opp_to * LG["live_to_share"]
            trans_lift = 1.0 + p_trans * (LG["trans_make"] - 1.0)
        num = den = 0.0
        for p in players:
            r = self.rates[p["pid"]]
            upp = r["FG2A_36"] + r["FG3A_36"] + 0.44 * r["FTA_36"] + r["TOV_36"]
            if upp <= 0:
                continue
            q = np.array([r["FG2A_36"], r["FG3A_36"], 0.44 * r["FTA_36"], r["TOV_36"]]) / upp
            # the engine now resolves shots by ZONE, so the closed form has to
            # value them the same way or the calibration drifts
            v2 = self._zone_value(p["pid"], False)
            v3 = self._zone_value(p["pid"], True)
            e2 = v2 if v2 is not None else 2 * r["FG2_PCT"]
            e3 = v3 if v3 is not None else 3 * r["FG3_PCT"]
            e1 = q[0] * e2 + q[1] * e3 + q[2] * 2.27 * r["FT_PCT"]
            cont = (q[0] * (1 - e2 / 2.0) + q[1] * (1 - e3 / 3.0)) * p_oreb
            ppp = (e1 / (1 - cont) if cont < 0.95 else e1) * trans_lift
            w = p["minutes"] * upp          # possessions this player is likely to use
            num += w * ppp
            den += w
        return num / den if den > 0 else 0.0

    def _pdraw(self, pool, seed, sim, pid, salt):
        """COMMON RANDOM NUMBERS: a player's draw is keyed to (seed, sim, player),
        not to his position in a shared stream. So when a scenario changes the
        roster, everyone ELSE receives byte-identical draws, and the difference
        between scenarios reflects the roster change instead of fresh sampling
        noise. Without this, comparing 'Jokic in' to 'Jokic out' compares two
        independent noisy estimates and the contrast is far less precise.
        """
        r = np.random.default_rng([int(seed), int(sim), int(pid), int(salt)])
        return float(pool[r.integers(len(pool))])

    def _jitter(self, M, rng, buckets=None, sd=0.24, pids=None, seed=0, sim=0):
        """Perturb a rotation, then restore the 5-on-court constraint.

        Draws each player's minutes multiplier from the EMPIRICAL ratio pool for
        his projected-minutes band, so bench volatility and early exits are
        represented at their real frequency instead of a uniform Gaussian.
        """
        if self.min_pools is not None and buckets is not None:
            if pids is not None:
                f = np.array([self._pdraw(self.min_pools[b], seed, sim, q, 1)
                              for b, q in zip(buckets, pids)])
                f = 1.0 + MIN_JITTER_SHRINK * (f - 1.0)
            else:
                f = np.array([rng.choice(self.min_pools[b]) for b in buckets])
        else:
            f = np.exp(rng.normal(0.0, sd, size=M.shape[0]))
        Mj = M * f[:, None]
        np.clip(Mj, 0.0, 1.0, out=Mj)
        for _ in range(40):
            col = Mj.sum(axis=0)
            col[col <= 0] = 1e-9
            Mj *= 5.0 / col
            np.clip(Mj, 0.0, 1.0, out=Mj)
            if abs(Mj.sum(axis=0) - 5).max() < 1e-2:
                break
        return Mj

    def _lineups(self, pids, M, rng):
        """Sample a concrete on-court five per slot, sticky across slots."""
        out = np.zeros((N_SLOTS, 5), dtype=np.int64)
        prev = set()
        arr = np.array(pids)
        for s in range(N_SLOTS):
            w = M[:, s].astype(float).copy()
            if prev:
                w *= np.where(np.isin(arr, list(prev)), 1.85, 1.0)
            w = np.clip(w, 1e-9, None)
            pick = rng.choice(len(arr), 5, replace=False, p=w / w.sum())
            out[s] = arr[pick]
            prev = set(arr[pick].tolist())
        return out

    # ---- possession outcome ----
    def _zone(self, pid):
        return self.zones.get(pid, self.zone_fb)

    def _zone_value(self, pid, three: bool) -> float:
        """Expected points per attempt for this player's zone mix on 2s or 3s."""
        z = self._zone(pid)
        if not z:
            return None
        names = ZONES3 if three else ZONES2
        val = 3.0 if three else 2.0
        tot = sum(z.get(f"sh_{c}", 0.0) for c in names)
        if tot <= 0:
            return None
        return sum(z.get(f"sh_{c}", 0.0) / tot * z.get(f"fg_{c}", 0.4) * val for c in names)

    def _draw_zone(self, pid, three: bool, rng, transition=False):
        z = self._zone(pid)
        names = ZONES3 if three else ZONES2
        if not z:
            return None, None
        w = np.array([max(z.get(f"sh_{c}", 0.0), 1e-9) for c in names])
        # No transition shift here. Tested at rim multipliers up to 3x and the
        # zone mix alone moves total scoring only 0.29%, against the ~1% the
        # measured transition premium requires — rim share is already high, so
        # there is little headroom. The premium therefore stays on the make
        # probability (trans_make), which is what the anchor accounts for.
        # Applying BOTH double-counted it and drifted the anchor +1.5 pts.
        i = rng.choice(len(names), p=w / w.sum())
        return names[i], z.get(f"fg_{names[i]}", 0.4)

    def _profile(self, pid):
        r = self.rates[pid]
        upp = max(r["FG2A_36"] + r["FG3A_36"] + 0.44 * r["FTA_36"] + r["TOV_36"], 1e-6)
        return (np.array([r["FG2A_36"], r["FG3A_36"], 0.44 * r["FTA_36"], r["TOV_36"]]) / upp,
                upp, r)

    def simulate(self, home_players, away_players, home_tid, away_tid,
                 n_sims=1000, seed=0, anchor=None, anchor_ref=None, pace_pair=None):
        """anchor: optional (mu_home, mu_away) expected team points from a
        top-down rating. Team strength is overwhelmingly the dominant signal in
        game prediction (~80x any box feature in your studies), and a sim built
        purely from individual player rates cannot recover it — script 100 found
        bottom-up loses to top-down, and this engine reproduces that. So the
        level is set top-down while the ALLOCATION across players, possessions
        and lineups stays bottom-up: the part a rating model cannot give you."""
        rng = np.random.default_rng(seed)
        self.team_scale = {"H": 1.0, "A": 1.0}
        sides = {}
        for tag, pl, tid in (("H", home_players, home_tid), ("A", away_players, away_tid)):
            pids, M = self.occupancy(pl)
            mm = {p["pid"]: p["minutes"] for p in pl}
            sides[tag] = {"players": pl, "pids": pids, "M": M, "tid": tid,
                          "buckets": [min_bucket(mm.get(q, 12.0)) for q in pids]}

        pace = ((pace_pair[0] + pace_pair[1]) / 2.0 if pace_pair
                else (self.pace_map.get(home_tid, self.lg_pace)
                      + self.pace_map.get(away_tid, self.lg_pace)) / 2.0)

        if anchor is not None and all(np.isfinite(anchor)):
            # Solve the scale ANALYTICALLY. A Monte-Carlo warm-up estimates each
            # team's mean to only +-2.4 pts on 24 runs, which injects more
            # calibration noise than it removes; the closed form has none.
            ref = anchor_ref or {}
            for tag, target in (("H", anchor[0]), ("A", anchor[1])):
                # Calibrate the level against the REFERENCE roster (everyone
                # available), then hold it fixed. Recomputing it against the
                # depleted roster would silently cancel the absence out — the
                # team would be rescaled right back to full strength.
                base = ref.get(tag) or sides[tag]["players"]
                other = "A" if tag == "H" else "H"
                base_opp = ref.get(other) or sides[other]["players"]
                ppp = self._team_ppp(base, base_opp)
                if ppp <= 0 or pace <= 0:
                    continue
                scale = float(np.clip((target / pace) / ppp, 0.80, 1.25))
                # roster delta in points/100 -> points/possession
                d_bpm = self.team_bpm(sides[tag]["players"]) - self.team_bpm(base)
                self.team_scale[tag] = float(np.clip(scale * (1.0 + (d_bpm / 100.0) / ppp),
                                                     0.55, 1.35))

        res = {"H": np.zeros(n_sims), "A": np.zeros(n_sims)}
        box = {t: defaultdict(lambda: defaultdict(lambda: np.zeros(n_sims)))
               for t in ("H", "A")}

        for sim in range(n_sims):
            # reseed per simulation so scenario A and scenario B enter each
            # replicate from an identical state; pace in particular is then a
            # shared draw rather than an independent one
            rng = np.random.default_rng([int(seed), int(sim), 99991])
            npos = max(60, int(rng.normal(pace, 4.0)))
            # per-simulation usage variation: some nights a role player never
            # gets going, which is what produces genuine scoreless outings
            if self.use_pool is not None:
                allp = [q for t in sides for q in sides[t]["pids"]]
                # Shrink toward 1: the raw per-game ratio also contains variance
                # the engine already models (minutes, matchup, shot luck), so
                # applying it undamped double-counts and over-widens the bands.
                draws = np.array([1.0 + USE_SHRINK
                                  * (self._pdraw(self.use_pool, seed, sim, q, 2) - 1.0)
                                  for q in allp])
                self._use_mult = dict(zip(allp, np.clip(draws, 0.05, None)))
            # Minutes are PROJECTED, not known: the production model's own error is
            # ~4.6 min MAE. Resampling the rotation each simulation propagates that
            # uncertainty into the player distributions — without it the intervals
            # are far too narrow (80% bands covered only 70% of outcomes).
            lu = {t: self._lineups(sides[t]["pids"],
                                   self._jitter(sides[t]["M"], rng,
                                                sides[t]["buckets"],
                                                pids=sides[t]["pids"],
                                                seed=seed, sim=sim), rng)
                  for t in sides}
            # A team's shooting travels together on a given night — makes are not
            # independent across teammates. Without a shared multiplier the team
            # total is under-dispersed, the classic independent-possession flaw.
            shared = float(np.exp(rng.normal(0, SHOOT_SD_SHARED)))
            self._hot = {t: shared * float(np.exp(rng.normal(0, SHOOT_SD_TEAM)))
                         for t in sides}
            fouls = {t: defaultdict(int) for t in sides}
            out = {t: set() for t in sides}
            trans = {t: False for t in sides}
            garbage = False
            # possessions ALTERNATE, so the running score is meaningful and
            # garbage time can be detected as it happens
            for i in range(npos):
                slot = min(int(i / npos * N_SLOTS), N_SLOTS - 1)
                for off, dfn in (("H", "A"), ("A", "H")):
                    late = i > 0.88 * npos
                    on = self._active(lu[off][slot], out[off], sides[off]["pids"],
                                      rng, garbage, fouls[off], not late)
                    dv = self._active(lu[dfn][slot], out[dfn], sides[dfn]["pids"],
                                      rng, garbage, fouls[dfn], not late)
                    pts_p, live_to = self._possession(
                        off, dfn, on, dv, rng, box, sim, fouls[dfn], out[dfn],
                        rem=npos - i, margin=res[off][sim] - res[dfn][sim],
                        transition=trans.get(off, False))
                    res[off][sim] += pts_p
                    # a live-ball turnover hands the OTHER team a fast break
                    trans[dfn] = live_to
                    trans[off] = False
                if i > 0.75 * npos and abs(res["H"][sim] - res["A"][sim]) > GARBAGE_MARGIN:
                    garbage = True
            # overtime — a tie has to be played out, otherwise the margin
            # distribution has an impossible spike at exactly zero
            ot = 0
            while res["H"][sim] == res["A"][sim] and ot < 4:
                ot += 1
                extra = max(6, int(npos * 5.0 / 48.0))
                for off, dfn in (("H", "A"), ("A", "H")):
                    for _ in range(extra):
                        on = self._active(lu[off][N_SLOTS - 1], out[off],
                                          sides[off]["pids"], rng, False)
                        dv = self._active(lu[dfn][N_SLOTS - 1], out[dfn],
                                          sides[dfn]["pids"], rng, False)
                        pts_p, _ = self._possession(off, dfn, on, dv, rng,
                                                    box, sim, fouls[dfn], out[dfn])
                        res[off][sim] += pts_p
        return res, box, sides

    def _active(self, five, out, pool, rng, garbage, fouls=None, protect=False):
        """The five who can actually play.

        Disqualified players are replaced; in garbage time the starters give way
        to the bench; and a player carrying FOUL_BENCH fouls is sat down until
        late, which is why real foul-outs are rare (~0.15/game) even though fouls
        themselves are common. Modelling fouls without that coaching response
        produced 0.68 foul-outs per game, over four times the real rate.
        """
        cur = [int(p) for p in five if int(p) not in out]
        if protect and fouls is not None:
            risky = [p for p in cur if fouls.get(p, 0) >= FOUL_BENCH]
            if risky:
                bench = [int(b) for b in pool
                         if b not in out and b not in cur
                         and fouls.get(int(b), 0) < FOUL_BENCH]
                for r in risky:
                    if bench:
                        cur[cur.index(r)] = bench.pop(0)
        if garbage and len(cur) == 5:
            bench = [p for p in pool if p not in out and p not in cur]
            if len(bench) >= 2:
                cur = cur[2:] + [int(b) for b in bench[:2]]
        if len(cur) < 5:
            spare = [int(p) for p in pool if p not in out and p not in cur]
            cur += spare[:5 - len(cur)]
        return np.array(cur[:5]) if len(cur) >= 5 else np.asarray(five)

    def _possession(self, off, dfn, on, dfive, rng, box, sim,
                    dfouls=None, dout=None, rem=99, margin=0.0,
                    transition=False):
        """One possession, played out through offensive rebounds.

        A possession is not one shot: ~23% of misses are rebounded by the offense
        and become another attempt inside the SAME possession. Ending at the first
        shot silently deletes every putback and depresses scoring by ~10%.
        """
        total = 0
        live_to = False
        for _ in range(4):                       # putback chains beyond this are rare
            use_w = np.array([(self.rates[p]["FG2A_36"] + self.rates[p]["FG3A_36"]
                               + 0.44 * self.rates[p]["FTA_36"] + self.rates[p]["TOV_36"])
                              * self._use_mult.get(p, 1.0) + 1e-9 for p in on])
            user = on[rng.choice(5, p=use_w / use_w.sum())]
            probs, _, r = self._profile(user)

            # matched defender: positional affinity over the five on court
            opos = self.pos.get(user, "F")
            aw = np.array([self.aff[POSITIONS.index(self.pos.get(d, "F"))]
                           [POSITIONS.index(opos)] for d in dfive])
            if self.heights:
                oh = self.heights.get(int(user))
                if oh is not None:
                    aw = aw * np.array([
                        height_mult(self.heights[int(d)] - oh)
                        if int(d) in self.heights else 1.0 for d in dfive])
            if self.weights:
                ow = self.weights.get(int(user))
                if ow is not None:
                    aw = aw * np.array([
                        weight_mult(self.weights[int(d)] - ow)
                        if int(d) in self.weights else 1.0 for d in dfive])
            defender = dfive[rng.choice(5, p=aw / aw.sum())]
            # defender quality shifts the make probability (deliberately small —
            # your studies show on-ball defence is a modest slice of the outcome)
            adj = ((1.0 - 0.010 * self.defq.get(defender, 0.0))
                   * self.team_scale[off] * getattr(self, "_hot", {}).get(off, 1.0))
            if transition:
                adj *= LG["trans_make"]

            def charge(d):
                """Book a personal foul; disqualify at the limit."""
                if dfouls is None or (dout is not None and int(d) in dout):
                    return                       # already disqualified — cannot foul again
                dfouls[d] += 1
                box[dfn][d]["PF"][sim] += 1
                if dfouls[d] >= FOUL_OUT and dout is not None:
                    dout.add(int(d))

            # non-shooting fouls, weighted by how foul-prone each defender is
            if dfouls is not None and rng.random() < NONSHOOT_FOUL:
                fw = np.array([self.rates[d]["PF_36"] + 1e-6 for d in dfive])
                charge(int(dfive[rng.choice(5, p=fw / fw.sum())]))

            # endgame: a trailing offence chases threes, and a trailing DEFENCE
            # fouls deliberately to get the ball back
            if transition:                       # fewer threes on the break
                q = probs.copy()
                moved = q[1] * (1 - LG["trans_3share"])
                q[1] -= moved
                q[0] += moved
                probs = q / q.sum()
            if rem <= 4 and margin < 0:
                q = probs.copy()
                mult = LG["late3_final"] if rem <= 1 else LG["late3_2min"]
                shift = min(q[0] * (mult - 1.0) * q[1] / max(q[0] + q[1], 1e-9), q[0])
                q[1] += shift
                q[0] -= shift
                probs = q / q.sum()
            if (rem <= 1 and dfouls is not None and -9 <= -margin <= -1
                    and rng.random() < LG["hack_prob"]):
                charge_target = int(dfive[rng.choice(5)])
                if dout is None or charge_target not in dout:
                    dfouls[charge_target] += 1
                    box[dfn][charge_target]["PF"][sim] += 1
                nft = 2
                pts = sum(1 for _ in range(nft)
                          if rng.random() < np.clip(r["FT_PCT"], 0.3, 0.99))
                box[off][user]["FTA"][sim] += nft
                if pts:
                    box[off][user]["PTS"][sim] += pts
                return total + pts, live_to

            k = rng.choice(4, p=probs)
            pts = 0
            if k == 0:                                        # two-point try
                zn, zfg = self._draw_zone(user, False, rng, transition)
                base_p = zfg if zfg is not None else r["FG2_PCT"]
                made = rng.random() < np.clip(base_p * adj, 0.05, 0.95)
                pts = 2 if made else 0
                box[off][user]["FGA"][sim] += 1
                box[off][user]["FGM"][sim] += made
            elif k == 1:                                      # three-point try
                zn, zfg = self._draw_zone(user, True, rng)
                base_p = zfg if zfg is not None else r["FG3_PCT"]
                made = rng.random() < np.clip(base_p * adj, 0.05, 0.85)
                pts = 3 if made else 0
                box[off][user]["FGA"][sim] += 1
                box[off][user]["FGM"][sim] += made
                box[off][user]["FG3M"][sim] += made
            elif k == 2:
                # 0.44*FTA counts possession-ENDING trips, so a trip averages
                # ~2.27 attempts (and-1s, three-shot fouls). Awarding a flat 2
                # would systematically under-count free-throw scoring.
                charge(int(defender))          # somebody fouled to send him there
                nft = 2 + (1 if rng.random() < 0.27 else 0)
                for _ in range(nft):
                    if rng.random() < np.clip(r["FT_PCT"], 0.3, 0.99):
                        pts += 1
                box[off][user]["FTA"][sim] += nft
            else:                                             # turnover
                box[off][user]["TOV"][sim] += 1
                live_to = rng.random() < LG["live_to_share"]
                # 56.6% of turnovers are steals; credit one to a defender
                if rng.random() < LG["stl_share"]:
                    sw = np.array([self.rates[d]["STL_36"] + 1e-6 for d in dfive])
                    box[dfn][dfive[rng.choice(5, p=sw / sw.sum())]]["STL"][sim] += 1

            if pts:
                box[off][user]["PTS"][sim] += pts
                total += pts
                if k in (0, 1):
                    # assist likelihood scales with how much this five actually
                    # passes — league AST-per-make spans 0.514 to 0.750
                    a_s = sum(self.rates[q]["AST_36"] for q in on) / self._ref["AST_36"]
                    p_ast = float(np.clip((LG["ast3"] if k == 1 else LG["ast2"]) * a_s,
                                          0.05, 0.95))
                    if rng.random() < p_ast:
                        mates = [p for p in on if p != user]
                        aw2 = np.array([self.rates[p]["AST_36"] + 1e-6 for p in mates])
                        box[off][mates[rng.choice(len(mates), p=aw2 / aw2.sum())]]["AST"][sim] += 1
                return total, live_to
            if k in (0, 1):                                   # miss -> live rebound
                # A miss is contested by the five men actually on the floor, so
                # the OREB rate has to move with them. Team OREB% ranges 0.154 to
                # 0.350 across games; a single constant discards all of it.
                # log5 odds: league odds scaled by offensive crashing over
                # defensive rebounding, both relative to a league-average five.
                off_s = sum(self.rates[q]["OREB_36"] for q in on) / self._ref["OREB_36"]
                def_s = sum(self.rates[q]["DREB_36"] for q in dfive) / self._ref["DREB_36"]
                odds = (LG["oreb"] / (1 - LG["oreb"])) * (off_s / max(def_s, 1e-6))
                p_oreb = float(np.clip(odds / (1 + odds), 0.06, 0.50))
                if k == 0 and rng.random() < LG["blk_share"]:
                    bw = np.array([self.rates[d]["BLK_36"] + 1e-6 for d in dfive])
                    box[dfn][dfive[rng.choice(5, p=bw / bw.sum())]]["BLK"][sim] += 1
                # a share of misses are booked as TEAM rebounds (out of bounds,
                # deadball) and credited to no player — crediting every miss to
                # an individual inflated rebound totals by ~7%
                team_reb = rng.random() < LG["team_reb"]
                if rng.random() < p_oreb:
                    if not team_reb:
                        w = np.array([self.rates[p]["OREB_36"] + 1e-6 for p in on])
                        box[off][on[rng.choice(5, p=w / w.sum())]]["REB"][sim] += 1
                    continue                                  # offence keeps the ball
                if not team_reb:
                    w = np.array([self.rates[p]["DREB_36"] + 1e-6 for p in dfive])
                    box[dfn][dfive[rng.choice(5, p=w / w.sum())]]["REB"][sim] += 1
            return total, live_to
        return total, live_to


def load_game_context(gid: str, out_ids: set):
    """-> (game row, {tag: [player dicts]}) with `out_ids` removed."""
    games = pd.read_parquet(GAMES)
    g = games[games.GAME_ID == gid]
    if not len(g):
        raise SystemExit(f"game {gid} not found")
    g = g.iloc[0]
    pg = pd.read_parquet(PG, columns=["GAME_ID", "TEAM_ID", "PLAYER_ID", "MIN", "position"])
    rows = pg[pg.GAME_ID == gid]
    sides = {}
    for tag, tid in (("H", g.HOME_TEAM_ID), ("A", g.AWAY_TEAM_ID)):
        d = rows[(rows.TEAM_ID == tid) & (rows.MIN > 0)]
        sides[tag] = [{"pid": int(r.PLAYER_ID), "minutes": float(r.MIN),
                       "started": int(isinstance(r.position, str) and bool(r.position.strip()))}
                      for r in d.itertuples() if int(r.PLAYER_ID) not in out_ids]
    return g, sides


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--sims", type=int, default=1000)
    ap.add_argument("--out", default="", help="comma-separated PLAYER_IDs to rule INACTIVE")
    args = ap.parse_args()
    out_ids = {int(x) for x in args.out.split(",") if x.strip()}

    g, sides = load_game_context(args.game, out_ids)
    rates, pos = build_rates(g.SEASON)
    args_game = args.game
    tmpl = pd.read_parquet(TMPL)
    aff = pd.read_parquet(AFF).set_index("dpos")[POSITIONS].to_numpy()
    _dq = defensive_index(g.SEASON)
    if _dq and isinstance(next(iter(_dq)), tuple):
        defq = {p: v for (gg, p), v in _dq.items() if gg == args.game}
    else:
        defq = _dq
    if not defq:
        dq = pd.read_parquet(DQ)
        dq = dq[dq.SEASON == g.SEASON]
        defq = {int(r.PLAYER_ID): float(r.DEF_RATING) for r in dq.itertuples()}
    pace_map, lg_pace = team_pace(g.SEASON)
    w3 = pd.read_parquet(ROOT / "data" / "parquet" / "player_seasons_war_v3.parquet")
    w3 = w3[w3.SEASON == g.SEASON]
    bpm = {int(r.PLAYER_ID): float(r.BPM3) for r in w3.itertuples()}
    s1 = pd.read_parquet(ROOT / "data" / "features" / "sim_mode1_predictions.parquet")
    s1 = s1[s1.GAME_ID == args.game]
    anchor = (float(s1.MU_HOME.iloc[0]), float(s1.MU_AWAY.iloc[0])) if len(s1) else None
    _, full = load_game_context(args.game, set())          # roster before scratches

    # minutes freed by the inactive players are redistributed across the rest
    for tag in sides:
        tot = sum(p["minutes"] for p in sides[tag])
        if tot > 0:
            for p in sides[tag]:
                p["minutes"] *= 240.0 / tot

    sim = Simulator(rates, pos, tmpl, aff, defq, pace_map, lg_pace, bpm,
                    minutes_ratio_pools(g.SEASON), usage_ratio_pool(g.SEASON),
                    *build_zones(g.SEASON), *load_heights())
    res, box, meta = sim.simulate(sides["H"], sides["A"], g.HOME_TEAM_ID,
                                  g.AWAY_TEAM_ID, n_sims=args.sims,
                                  anchor=anchor, anchor_ref=full)

    h, a = res["H"], res["A"]
    margin = h - a
    print(f"\n{g.AWAY_TEAM} @ {g.HOME_TEAM}   {str(g.GAME_DATE)[:10]}   "
          f"({args.sims:,} simulations)")
    if out_ids:
        print(f"  ruled OUT: {sorted(out_ids)}")
    print(f"\n  home win probability   {(margin > 0).mean()*100:5.1f}%")
    print(f"  projected score        {h.mean():.1f} - {a.mean():.1f}")
    print(f"  margin  mean {margin.mean():+.1f}  sd {margin.std():.1f}  "
          f"p10/p90 {np.percentile(margin,10):+.0f}/{np.percentile(margin,90):+.0f}")
    tot = h + a
    print(f"  total   mean {tot.mean():.1f}  sd {tot.std():.1f}  "
          f"p10/p90 {np.percentile(tot,10):.0f}/{np.percentile(tot,90):.0f}")
    print(f"\n  ACTUAL: {g.HOME_PTS:.0f} - {g.AWAY_PTS:.0f} "
          f"(margin {g.MARGIN:+.0f}, total {g.TOTAL:.0f})")

    nm = pd.read_parquet(PS, columns=["PLAYER_ID", "SEASON", "PLAYER"])
    nm = {r.PLAYER_ID: r.PLAYER for r in nm[nm.SEASON == g.SEASON].itertuples()}
    actual = pd.read_parquet(PG, columns=["GAME_ID", "PLAYER_ID", "points", "reboundsTotal", "assists"])
    actual = {int(r.PLAYER_ID): r.points for r in actual[actual.GAME_ID == args.game].itertuples()}
    for tag, label in (("H", g.HOME_TEAM), ("A", g.AWAY_TEAM)):
        print(f"\n  {label} projected player lines (pts, 80% interval):")
        rank = sorted(box[tag].items(), key=lambda kv: -kv[1]["PTS"].mean())[:8]
        for pid, st in rank:
            p = st["PTS"]
            act = actual.get(pid)
            astr = f"{act:>5.0f}" if act is not None else "    -"
            print(f"    {nm.get(pid,pid)!s:<24}{p.mean():>6.1f}  "
                  f"[{np.percentile(p,10):>4.0f}-{np.percentile(p,90):<4.0f}]  actual{astr}")


if __name__ == "__main__":
    main()
