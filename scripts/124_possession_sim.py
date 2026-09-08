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
ROOKIE = ROOT / "data" / "parquet" / "rookie_priors.parquet"
AGING = ROOT / "data" / "parquet" / "aging_curves.parquet"
# Only columns whose age adjustment beat the age-blind projection WALK-FORWARD
# (script 139). Usage and role age predictably; rebounding, steals, blocks and
# three-point accuracy do not, and adjusting them made projections worse.
# Columns whose age adjustment beat the age-blind projection walk-forward with a
# 95% cluster-bootstrap interval ENTIRELY BELOW ZERO (script 139). An earlier
# version of this list was chosen on the SIGN of the held-out change alone and
# included five columns — AST_36, FGA_36, TOV_36, PF_36, FT_PCT — whose intervals
# comfortably span zero. Of the rate columns the engine consumes, only these two
# survive a real test. PTS_36 and TS_PCT also validate but are not engine inputs;
# the engine builds scoring from attempts and percentages, none of which clear
# significance individually, which is why no efficiency ageing is applied here.
AGE_APPLY = {"MPG": 1.0, "FTA_36": 1.0}

RECENCY = {1: 5.0, 2: 4.0, 3: 3.0}
K = 1000.0
SLOT_SEC, N_SLOTS = 30.0, 96
POSITIONS = ["G", "F", "C"]

# league constants, calibrated from our own data in calibrate()
USE_SHRINK = 0.72     # damping on per-game usage variation
MIN_JITTER_SD = 0.24     # per-player minutes resampling spread
MIN_JITTER_SHRINK = 1.0  # damping on per-player minutes variation (no effect on team spread)
FOUL_OUT = 6          # personal fouls that disqualify
NONSHOOT_FOUL = 0.092 # per-possession chance of a non-shooting foul
FOUL_BENCH = 5        # coaches sit a player on this many fouls until late
# THE PENALTY (script 144). After PENALTY_LIMIT team fouls in a period, every
# non-shooting defensive foul is two free throws. Player FTA_36 ALREADY contains
# these attempts, so this is a SPLIT, not an addition: PEN_FT_TRIM is removed
# from the base free-throw branch and re-routed through the team-foul counter.
# Same season total, but now concentrated late in periods and responsive to
# intentional fouling, which is where win probability is decided.
# MEAN REVERSION. The engine's independent team-score variance is 141.7 against
# a real 126.0 (script 143 decomposition), and its floor with every injected
# noise source at zero is 139.3 — so the excess is the possession-level binomial
# floor and CANNOT be removed by turning knobs down. Real possessions must carry
# a small negative serial correlation, about -0.0011 per pair.
#
# Strength is DERIVED, not tuned. Feeding a team's running deviation from
# expectation back into its scoring rate makes that deviation an AR(1); its
# variance after n possessions is sigma^2 (1-e^-2nk)/(2k), so the ratio to the
# independent n*sigma^2 is (1-e^-2nk)/(2nk). Setting that to 126.0/141.7 = 0.889
# at n=98 gives 2nk = 0.24, k = 0.00122 points per point of deviation, and as a
# multiplier on a 1.13 ppp offence, kappa = 0.00108. A team 10 points above its
# expected pace has its scoring rate cut by 1.1%.
#
# The MECHANISM is unidentified — script 147 could not confirm own-deviation
# reversion directly, because demeaning a team by its own game mean manufactures
# an identical pattern. The MAGNITUDE is measured from game-level moments, which
# that artifact does not touch.
# 0.00108 was DERIVED to match the unconditional league margin spread (15.88).
# That was the wrong target. A simulator forecasting a KNOWN matchup should
# match its own conditional error, not the league-wide spread which also
# contains the variation between good and bad matchups that the forecast is
# supposed to PREDICT rather than sample. Swept on 2023-24 and confirmed on
# 2024-25 (script 148), the derived value was worse than no reversion at all on
# both seasons; 0.0055 drives the ratio of predictive sd to residual sd to
# 1.013 and improves held-out log-loss and Brier on a season it never saw.
MEAN_REVERT = 0.0055      # strength on the DIFFERENTIAL (margin) deviation
# A team's deviation from expectation splits into a part it SHARES with its
# opponent (both scoring above pace -> a high-total game) and a part that is
# differential (one team ahead -> margin). Reverting the raw own-deviation
# applies one strength to both, and at the value that calibrates margin it
# over-compressed totals: predictive total sd 17.06 against a realised RMSE of
# 18.43, a ratio of 0.926. Splitting them lets margin stay calibrated while
# totals keep the spread they actually need.
MEAN_REVERT_TOT = 0.0030  # strength on the COMMON (total) deviation
PENALTY_LIMIT = 5
PEN_FT_TRIM = 0.137   # measured share of all FTs coming from penalty fouls
# Penalty free-throw TRIPS per possession, measured FROM THE ENGINE rather than
# from real games. The anchor has to expect what the simulator actually does:
# real games reach the penalty on 21.4% of non-shooting fouls but this engine
# reaches it on 14.0%, and the non-shooting draw fires inside the putback loop
# so its effective per-possession rate is 0.102, not NONSHOOT_FOUL's 0.092.
# Using the real-game numbers here over-credited the anchor by 0.8 pts/team and
# pulled the engine 1.8 points below its target — the anchor breaking a fourth
# time, in the usual way: a closed form that did not know what was added.
PEN_TRIPS = 0.0142
# diagnostic counters: the anchor's closed form assumes how often the engine is
# actually in the penalty, and that assumption has to be checked against the
# engine rather than against real games, which foul at a different rate
PEN_DIAG = {"nonshoot": 0, "in_pen": 0, "ft": 0}
GARBAGE_MARGIN = 18   # lead that empties the benches late
# The lineup sampler redraws every 30s slot, so without strong persistence it
# substitutes constantly: at the old value of 1.85 it produced 116 substitutions
# per team per game against a real 24.4, nearly 5x too many. That was invisible
# until the cold-start feature needed to know how long a player had been on the
# floor. 35 reproduces roughly the real rate.
# Random walk on the systematic-sampling offset. Systematic pi-ps gives exactly
# the right marginal inclusion probability in every slot, but with a FIXED
# offset the joint distribution across slots is severely correlated: a marginal
# player is either in for long stretches or out for the whole game, depending on
# where u happens to fall. Minutes come out right on average and BIMODAL across
# simulations, which is what left the engine giving low-minute players zero shot
# attempts 46.9% of the time against a real 26.7%.
# Drifting u decorrelates the draws without touching the marginals. At 0.06 the
# overall P(0 points) lands on 12.1%, exactly the observed rate, and the
# substitution count barely moves (31.2 -> 31.7 per team-game) because churn is
# driven by the occupancy curves rather than by u.
LINEUP_DRIFT = 0.06
# A random walk MIXES TOO SLOWLY to decorrelate a game. With step 0.06 it needs
# roughly (1/0.06)^2 ~ 280 moves to traverse the unit interval, and a game has
# only ~19 redraw windows, so u barely travels and a marginal player's inclusion
# stays correlated from tip-off to final buzzer. Adding a golden-ratio increment
# sweeps u across the whole interval in a low-discrepancy sequence instead: each
# player then receives close to his marginal share of windows in EVERY
# simulation rather than all-or-nothing across simulations. 0.0 recovers the
# pure random walk.
# TESTED AND REJECTED at 0.618. The mixing diagnosis was right — a random walk
# of step 0.06 cannot traverse the interval in ~19 windows — but the fix trades
# one error for a larger one. Sweeping removes minutes variance EVERYWHERE,
# while only the bottom tier has too much of it:
#     tier      (0,8]   (8,14]  (14,20]     zero-rate gap vs actual
#     sweep 0    +6.4     -2.6     -1.1
#     sweep .618 +2.3     -9.9     -5.4
# Weighted by tier size the sweep more than doubles total error, because the
# middle tiers hold four times as many players and genuinely NEED the
# all-or-nothing variation the sweep removes. The bottom tier's excess is a
# different defect and needs a different instrument.
LINEUP_SWEEP = 0.0
LINEUP_HOLD = 5        # slots the five is held before redrawing (30s each).
                       # 1 -> 44 substitutions per team-game, 5 -> ~25 against a
                       # real 24.4; minutes accuracy is flat across the range,
                       # so this trades nothing to buy rotation realism.
LINEUP_PERSISTENCE = 35.0  # retained for reference; the pi-ps sampler in
                       # _lineups no longer uses it
# Shooting luck splits into a SHARED game component (both teams shoot well in
# the same loose game) and a TEAM-specific one. Only the team-specific part moves
# the margin; the shared part moves the total. Drawing it all as team-specific
# over-dispersed the margin (sd 17.8 against an actual residual sd of ~14.5),
# which makes win probabilities under-confident.
PACE_SD = 4.0         # game-to-game possession noise, SHARED by both teams
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
      # COLD START. A player's first minute on the floor is worth ~0.045 fewer
      # points per shot than his own norm, replicating across seasons (-0.039 and
      # -0.043) and monotone thereafter (-0.008, +0.002, +0.006, +0.008). It is
      # substitution entries, not tip-off: mid-game entries show -0.037 and -0.049.
      # Note this is the OPPOSITE of fatigue, which was tested and found absent.
      "cold_make": 0.960,      # make-probability multiplier in the first minute
      "cold_share": 0.10,      # share of shots taken inside that window
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
    """Rate lookup with a DRAFT-SLOT-AWARE fallback.

    ~20% of players in a season (rookies, two-way call-ups) have no prior-season
    history and so no Marcel projection. Dropping them is NOT harmless: their
    minutes get redistributed to everyone else by the 240-minute rescale, which
    inflates every surviving player's line. They average 11% of team minutes and
    up to 77% in an extreme game. So unknown players get a replacement profile
    and keep their minutes instead of vanishing.
    """

    def __init__(self, d, fallback, slot_of=None, slot_profiles=None):
        super().__init__(d)
        self.fallback = fallback
        self.slot_of = slot_of or {}
        self.slot_profiles = slot_profiles or {}

    def __missing__(self, key):
        # A first overall pick and an undrafted two-way used to get identical
        # rates. Draft slot predicts ROLE strongly (minutes R2 0.285, FGA/36
        # 0.220, points/36 0.197) and skill not at all (every shooting
        # percentage R2 ~ 0), so the slot profile mostly reshapes volume.
        slot = self.slot_of.get(int(key))
        if slot is not None and slot in self.slot_profiles:
            return self.slot_profiles[slot]
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
    _apply_aging(rates, season)
    full = {p: r for p, r in rates.items() if len(r) == len(cols)}
    # replacement profile = median of every projected rate, with usage damped:
    # a player with no track record is a low-usage bench body, not a median starter
    med = {c: float(np.nanmedian([r[c] for r in full.values()])) for c in cols}
    for c in ("FG2A_36", "FG3A_36", "FTA_36", "AST_36"):
        med[c] *= 0.80
    med["MPG"] = 12.0
    return RateBook(full, med, *rookie_profiles(cols, med)), pos


def _apply_aging(rates: dict, season: str) -> None:
    """Shift each projection by one year of ageing, in place.

    A Marcel projection describes what a player has recently done; the target
    season is a year later, so the adjustment is curve(age) - curve(age-1).
    Applied only to the columns that validated walk-forward.
    """
    if not (AGING.exists() and BIO.exists()):
        return
    cur = pd.read_parquet(AGING)
    cur = cur[cur.pos == "ALL"]
    by = defaultdict(dict)
    for r in cur.itertuples():
        by[r.metric][int(r.age)] = float(r.effect)
    bio = pd.read_parquet(BIO, columns=["PLAYER_ID", "BIRTHDATE"]).dropna()
    mid = pd.Timestamp(int(season[:4]) + 1, 2, 1)
    age = {int(r.PLAYER_ID): (mid - r.BIRTHDATE).days / 365.25
           for r in bio.itertuples()}
    lo, hi = 20, 38
    for pid, prof in rates.items():
        a = age.get(int(pid))
        if a is None:
            continue
        an, ap = int(np.clip(round(a), lo, hi)), int(np.clip(round(a - 1), lo, hi))
        for col, w in AGE_APPLY.items():
            if w <= 0 or col not in prof or col not in by:
                continue
            d = by[col].get(an)
            q = by[col].get(ap)
            if d is None or q is None:
                continue
            prof[col] = max(prof[col] + w * (d - q), 0.0)


DRIFT_COLS = ("FG2A_36", "FG3A_36", "FTA_36", "TOV_36", "OREB_36",
              "DREB_36", "AST_36", "PF_36")


def league_drift(season: str) -> dict:
    """Per-game factors pinning the rate book's LEAGUE LEVEL to the season.

    Script 153. The book projects from prior seasons and lags real league
    trends: in 2024-25 it under-projected three-point attempts by 4.2% and
    offensive rebounds by 5.2% while over-projecting free throws by 3.1% and
    fouls by 3.5%, even after in-season updating. Scaling every player's rate by
    the same factor moves the aggregate without touching the SPREAD across
    players, which is the part the book is good at.

    Each game's factor uses only games played strictly before its date.
    """
    f = ROOT / "data" / "parquet" / "league_drift.parquet"
    if not f.exists():
        return {}
    d = pd.read_parquet(f)
    d = d[d.SEASON == season]
    cols = [c for c in DRIFT_COLS if c in d.columns]
    return {r.GAME_ID: {c: float(getattr(r, c)) for c in cols}
            for r in d.itertuples()}


def apply_drift(prof: dict, fac: dict) -> dict:
    """Scale one player's rate profile by the league factors."""
    if not fac:
        return prof
    out = dict(prof)
    for c, m in fac.items():
        if c in out and np.isfinite(m) and m > 0:
            out[c] = out[c] * m
    return out


def load_share_cal() -> dict:
    """{(player, stat): weight multiplier} solved by script 159.

    Rates are handed out in proportion to the on-court players' per-36 numbers,
    which pulls everyone toward the middle of their lineup: script 158 measured
    the top quintile 11-14% short on every stat. These factors restore the
    split. They cannot move a team total, because a share is normalised within
    the five.
    """
    f = ROOT / "data" / "parquet" / "share_cal.parquet"
    if not f.exists():
        return {}
    d = pd.read_parquet(f)
    return {(int(r.PLAYER_ID), str(r.stat)): float(r.cal) for r in d.itertuples()}


def rookie_profiles(cols, med):
    """-> ({player: draft slot}, {slot: rate profile}) for players with no history."""
    if not (ROOKIE.exists() and BIO.exists()):
        return {}, {}
    pri = pd.read_parquet(ROOKIE)
    bio = pd.read_parquet(BIO)
    slot_of = {}
    for r in bio.itertuples():
        d = str(r.DRAFT_NUMBER)
        slot_of[int(r.PLAYER_ID)] = 0 if d.lower().startswith("undraft") else (
            int(d) if d.isdigit() else None)
    slot_of = {k: v for k, v in slot_of.items() if v is not None}
    look = defaultdict(dict)
    for r in pri.itertuples():
        look[int(r.slot)][r.metric] = float(r.value)
    profiles = {}
    for slot, v in look.items():
        prof = dict(med)
        for c in cols:
            if c in v:
                prof[c] = v[c]
        # the simulator needs two derived columns the priors do not carry
        fga, fg3a = v.get("FGA_36", med["FGA_36"]), v.get("FG3A_36", med["FG3A_36"])
        prof["FG2A_36"] = max(fga - fg3a, 0.1)
        fgp, fg3p = v.get("FG_PCT", 0.45), v.get("FG3_PCT", 0.33)
        made = fgp * fga
        prof["FG2_PCT"] = float(np.clip((made - fg3p * fg3a) / max(fga - fg3a, 0.1),
                                        0.30, 0.70))
        profiles[slot] = prof
    return slot_of, profiles


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
        # Allocation-share corrections (script 159). Loaded here rather than by
        # each caller so every consumer — evaluation, matchup driver, web export
        # — gets the same engine, and a missing file simply means no correction.
        self.share_cal = load_share_cal()
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
        cold_lift = 1.0 - LG["cold_share"] * (1.0 - LG["cold_make"])
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
            ftw = self._ftw(r)
            upp = r["FG2A_36"] + r["FG3A_36"] + ftw + r["TOV_36"]
            if upp <= 0:
                continue
            q = np.array([r["FG2A_36"], r["FG3A_36"], ftw, r["TOV_36"]]) / upp
            # the engine now resolves shots by ZONE, so the closed form has to
            # value them the same way or the calibration drifts
            v2 = self._zone_value(p["pid"], False)
            v3 = self._zone_value(p["pid"], True)
            e2 = v2 if v2 is not None else 2 * r["FG2_PCT"]
            e3 = v3 if v3 is not None else 3 * r["FG3_PCT"]
            e1 = q[0] * e2 + q[1] * e3 + q[2] * 2.27 * r["FT_PCT"]
            cont = (q[0] * (1 - e2 / 2.0) + q[1] * (1 - e3 / 3.0)) * p_oreb
            ppp = (e1 / (1 - cont) if cont < 0.95 else e1) * trans_lift * cold_lift
            w = p["minutes"] * upp          # possessions this player is likely to use
            num += w * ppp
            den += w
        if den <= 0:
            return 0.0
        # The penalty branch does not ADD possessions, it REPLACES them: a
        # possession that would have produced the ordinary mix instead produces
        # a two-shot trip. So the anchor takes the INCREMENT, not the total.
        # Adding the total pulled the engine 1.1 points per team below target,
        # because the closed form then expected penalty points on top of every
        # normal possession rather than instead of a few of them.
        base = num / den
        ftp = sum(x["minutes"] * self.rates[x["pid"]]["FT_PCT"] for x in players) \
            / max(sum(x["minutes"] for x in players), 1e-9)
        return base + PEN_TRIPS * (2.0 * ftp - base)

    def cal(self, pid, stat: str) -> float:
        """Multiplier on this player's ALLOCATION weight for one stat.

        Shares are drawn in proportion to per-36 rates among the five on court,
        but a rate is earned across the mix of team-mates a player actually
        plays with. Inside one specific five, proportional weighting pulls
        everyone toward the middle: script 158 measures the top quintile
        under-produced by 11-14% on every stat and the bottom quintile
        over-produced by up to 40%. These factors are solved by script 159 so
        the engine reproduces the rate it was given.

        Because a share is normalised within the lineup, scaling weights moves
        only the SPLIT and never the team total, so this cannot disturb any
        team-level calibration.
        """
        c = self.share_cal.get((int(pid), stat))
        return 1.0 if c is None else float(c)

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

    def _jitter(self, M, rng, buckets=None, sd=None, pids=None, seed=0, sim=0):
        """Perturb a rotation, then restore the 5-on-court constraint.

        Draws each player's minutes multiplier from the EMPIRICAL ratio pool for
        his projected-minutes band, so bench volatility and early exits are
        represented at their real frequency instead of a uniform Gaussian.

        TWO TRAPS AROUND `sd`, BOTH OF WHICH SILENTLY BROKE AN ABLATION.

        It was declared `sd=MIN_JITTER_SD`, a default argument, and Python binds
        those once when the function is DEFINED — so setting the module constant
        afterwards changed nothing. It now resolves the global at CALL time.

        More importantly, `sd` only reaches the fallback Gaussian branch below.
        Whenever the empirical pools are loaded, which is the normal path, the
        spread is governed by MIN_JITTER_SHRINK instead and `sd` is unused. The
        variance budget in script 143 reported minutes jitter as contributing
        0.00% of margin spread; that was not a fact about the model, it was two
        layers of the knob not being connected to anything.
        """
        sd = MIN_JITTER_SD if sd is None else sd
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
        """Sample a concrete on-court five per slot, sticky across slots.

        M[:, s] is already the MARGINAL INCLUSION PROBABILITY of each player in
        slot s: `occupancy` builds it so every column sums to exactly five with
        every entry in [0, 1], and each row sums to that player's minutes.

        The previous implementation drew five without replacement with those as
        weights, multiplying the incumbent five by 1 + LINEUP_PERSISTENCE first.
        Weighted sampling WITHOUT REPLACEMENT does not reproduce its weights as
        marginal inclusion probabilities — heavy items crowd out light ones —
        and a 36x incumbency bonus made the crowding severe. Measured against
        assigned minutes, a player given 6 minutes received 2.5 (ratio 0.42)
        while starters got 7% MORE than assigned. Total minutes still summed to
        240, so nothing downstream flagged it; the minutes were silently
        redistributed from the bench to the starters, which is why the engine
        produced far too many scoreless bench lines.

        Systematic pi-ps sampling hits the marginals EXACTLY. Cumulate the
        probabilities to five, then take the players crossed by the five points
        u, u+1, ..., u+4. Because no probability exceeds one, consecutive points
        cannot land on the same player, so exactly five distinct bodies come
        back. Holding u across slots supplies persistence for free — the five
        change only as the occupancy curves themselves change — and a small
        random walk on u adds the residual churn real rotations show.
        """
        out = np.zeros((N_SLOTS, 5), dtype=np.int64)
        arr = np.array(pids)
        n = len(arr)
        if n <= 5:
            out[:] = np.resize(arr, 5)
            return out
        # ORDER MATTERS for churn. Systematic sampling walks a cumulative sum,
        # so which players sit next to each other decides who swaps for whom
        # when the curves shift. In an arbitrary order a tiny change in one
        # player's occupancy flips a crossing between two unrelated players and
        # the five churns far faster than a real rotation. Sorting by total
        # minutes puts similar players adjacent, so a crossing that does move
        # moves between plausible substitutes.
        order = np.argsort(-M.sum(axis=1))
        arr = arr[order]
        M = M[order]
        u = float(rng.random())
        offs = np.arange(5, dtype=float)
        # Coaches do not reconsider the lineup every thirty seconds. Re-drawing
        # each slot made the five churn about 44 times a team-game against a
        # real 24.4, because the sampler responds to every small movement in the
        # occupancy curves. Holding the draw across LINEUP_HOLD slots and using
        # the window's MEAN occupancy keeps the marginals right — the average of
        # the probabilities over the window is what the player is owed — while
        # cutting the substitution rate to something a rotation would produce.
        for s in range(N_SLOTS):
            if s % LINEUP_HOLD:
                out[s] = out[s - 1]
                continue
            win = M[:, s:s + LINEUP_HOLD]
            pi = np.clip(win.mean(axis=1).astype(float), 1e-12, 1.0)
            tot = pi.sum()
            if tot <= 0:
                pi = np.full(n, 5.0 / n)
            else:
                pi *= 5.0 / tot
            np.clip(pi, 0.0, 1.0, out=pi)
            # clipping at 1 loses mass; push it back onto players with headroom
            for _ in range(20):
                d = 5.0 - pi.sum()
                if abs(d) < 1e-9:
                    break
                room = pi < 1.0
                if not room.any() or pi[room].sum() <= 0:
                    break
                pi[room] += d * pi[room] / pi[room].sum()
                np.clip(pi, 0.0, 1.0, out=pi)
            idx = np.clip(np.searchsorted(np.cumsum(pi), u + offs, side="right"),
                          0, n - 1)
            out[s] = arr[idx]
            u = (u + LINEUP_SWEEP + rng.normal(0.0, LINEUP_DRIFT)) % 1.0
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

    @staticmethod
    def _ftw(r):
        """Possession-ending free-throw trips, less the share the penalty now
        supplies. Used by BOTH the possession mix and the anchor's closed form —
        trimming only one of them is how the anchor gets broken."""
        return 0.44 * r["FTA_36"] * (1.0 - PEN_FT_TRIM)

    def _profile(self, pid):
        r = self.rates[pid]
        ftw = self._ftw(r)
        upp = max(r["FG2A_36"] + r["FG3A_36"] + ftw + r["TOV_36"], 1e-6)
        return (np.array([r["FG2A_36"], r["FG3A_36"], ftw, r["TOV_36"]]) / upp,
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

        # expected points so far, used as the reference the offence reverts to
        mu_t = ({"H": float(anchor[0]), "A": float(anchor[1])}
                if anchor is not None and all(np.isfinite(anchor)) else None)
        res = {"H": np.zeros(n_sims), "A": np.zeros(n_sims)}
        box = {t: defaultdict(lambda: defaultdict(lambda: np.zeros(n_sims)))
               for t in ("H", "A")}

        for sim in range(n_sims):
            # reseed per simulation so scenario A and scenario B enter each
            # replicate from an identical state; pace in particular is then a
            # shared draw rather than an independent one
            rng = np.random.default_rng([int(seed), int(sim), 99991])
            npos = max(60, int(rng.normal(pace, PACE_SD)))
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
            # team fouls reset every period, which is what makes the penalty
            # path-dependent rather than a flat per-possession rate
            tfoul = {t: [0] for t in sides}
            cur_period = 0
            out = {t: set() for t in sides}
            trans = {t: False for t in sides}
            entered = {t: {} for t in sides}      # pid -> slot he came on
            lastseen = {t: {} for t in sides}     # pid -> last slot on the floor
            garbage = False
            # possessions ALTERNATE, so the running score is meaningful and
            # garbage time can be detected as it happens
            for i in range(npos):
                slot = min(int(i / npos * N_SLOTS), N_SLOTS - 1)
                per_i = min(int(i / npos * 4), 3)
                if per_i != cur_period:
                    cur_period = per_i
                    for t in tfoul:
                        tfoul[t][0] = 0
                for t in sides:
                    # A player counts as newly ENTERED only after a real absence.
                    # The lineup sampler redraws every 30s slot, so a one-slot
                    # flicker would otherwise reset his clock and mark him cold
                    # again — which over-applied the penalty and pulled the
                    # engine 2 points below its anchor.
                    cur = set(int(q) for q in lu[t][slot])
                    for q in cur:
                        gap = slot - lastseen[t].get(q, -99)
                        if gap >= 2:
                            entered[t][q] = slot
                        lastseen[t][q] = slot
                for off, dfn in (("H", "A"), ("A", "H")):
                    late = i > 0.88 * npos
                    on = self._active(lu[off][slot], out[off], sides[off]["pids"],
                                      rng, garbage, fouls[off], not late)
                    dv = self._active(lu[dfn][slot], out[dfn], sides[dfn]["pids"],
                                      rng, garbage, fouls[dfn], not late)
                    pts_p, live_to = self._possession(
                        off, dfn, on, dv, rng, box, sim, fouls[dfn], out[dfn],
                        rem=npos - i, margin=res[off][sim] - res[dfn][sim],
                        transition=trans.get(off, False),
                        cold={q: slot - e for q, e in entered[off].items()},
                        tfoul=tfoul[dfn],
                        dev=(res[off][sim] - mu_t[off] * (i / npos)
                             if mu_t is not None else 0.0),
                        dev_opp=(res[dfn][sim] - mu_t[dfn] * (i / npos)
                                 if mu_t is not None else 0.0))
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
                for t in tfoul:
                    tfoul[t][0] = 0
                for off, dfn in (("H", "A"), ("A", "H")):
                    for _ in range(extra):
                        on = self._active(lu[off][N_SLOTS - 1], out[off],
                                          sides[off]["pids"], rng, False)
                        dv = self._active(lu[dfn][N_SLOTS - 1], out[dfn],
                                          sides[dfn]["pids"], rng, False)
                        pts_p, _ = self._possession(off, dfn, on, dv, rng,
                                                    box, sim, fouls[dfn], out[dfn],
                                                    tfoul=tfoul[dfn])
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
                    transition=False, cold=None, tfoul=None, dev=0.0,
                    dev_opp=0.0):
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
                              * self._use_mult.get(p, 1.0) * self.cal(p, "USE")
                              + 1e-9 for p in on])
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
            if dev or dev_opp:
                # split the deviation: the half shared with the opponent drives
                # the TOTAL, the half that differs drives the MARGIN
                common = 0.5 * (dev + dev_opp)
                diff = 0.5 * (dev - dev_opp)
                pull = MEAN_REVERT * diff + MEAN_REVERT_TOT * common
                adj *= float(np.clip(1.0 - pull, 0.85, 1.15))
            if transition:
                adj *= LG["trans_make"]
            # two 30-second slots = the first minute on the floor
            if cold is not None and cold.get(int(user), 99) < 2:
                adj *= LG["cold_make"]

            def charge(d):
                """Book a personal foul; disqualify at the limit."""
                if dfouls is None or (dout is not None and int(d) in dout):
                    return                       # already disqualified — cannot foul again
                dfouls[d] += 1
                box[dfn][d]["PF"][sim] += 1
                if tfoul is not None:
                    tfoul[0] += 1            # team fouls drive the penalty
                if dfouls[d] >= FOUL_OUT and dout is not None:
                    dout.add(int(d))

            # non-shooting fouls, weighted by how foul-prone each defender is.
            # Under the limit these only book a personal foul. Once the defence
            # is in the penalty the same foul is two free throws and ends the
            # possession — the bonus, which the engine previously could not
            # represent at all.
            if dfouls is not None and rng.random() < NONSHOOT_FOUL:
                fw = np.array([self.rates[d]["PF_36"] * self.cal(d, "PF") + 1e-6
                               for d in dfive])
                charge(int(dfive[rng.choice(5, p=fw / fw.sum())]))
                PEN_DIAG["nonshoot"] += 1
                if tfoul is not None and tfoul[0] > PENALTY_LIMIT:
                    pts = sum(1 for _ in range(2)
                              if rng.random() < np.clip(r["FT_PCT"], 0.3, 0.99))
                    box[off][user]["FTA"][sim] += 2
                    PEN_DIAG["ft"] += 2
                    if pts:
                        box[off][user]["PTS"][sim] += pts
                    return total + pts, live_to

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
                    if tfoul is not None:
                        tfoul[0] += 1
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
                    sw = np.array([self.rates[d]["STL_36"] * self.cal(d, "STL") + 1e-6
                                   for d in dfive])
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
                        aw2 = np.array([self.rates[p]["AST_36"] * self.cal(p, "AST") + 1e-6
                                        for p in mates])
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
                    bw = np.array([self.rates[d]["BLK_36"] * self.cal(d, "BLK") + 1e-6
                                   for d in dfive])
                    box[dfn][dfive[rng.choice(5, p=bw / bw.sum())]]["BLK"][sim] += 1
                # a share of misses are booked as TEAM rebounds (out of bounds,
                # deadball) and credited to no player — crediting every miss to
                # an individual inflated rebound totals by ~7%
                team_reb = rng.random() < LG["team_reb"]
                if rng.random() < p_oreb:
                    if not team_reb:
                        w = np.array([self.rates[p]["OREB_36"] * self.cal(p, "REB") + 1e-6
                                      for p in on])
                        box[off][on[rng.choice(5, p=w / w.sum())]]["REB"][sim] += 1
                    continue                                  # offence keeps the ball
                if not team_reb:
                    w = np.array([self.rates[p]["DREB_36"] * self.cal(p, "REB") + 1e-6
                                  for p in dfive])
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
