"""Point-in-time substrate for experiments, independent of fitted simulator files.

Candidates are the previous box-score roster, including DNPs. At a trade or
season opener this is deliberately an imperfect proxy for a dated roster feed.
Actual-only additions are retained with forecastable=False and zero predictions
in evaluation, so missing a debut cannot make the score look better.
All games on a date are predicted before any result from that date is ingested.
"""
from collections import defaultdict, deque

import numpy as np
import pandas as pd

from minutes_normalization import normalize_minutes

TARGETS = {"minutes": "MIN", "points": "points", "reb": "reboundsTotal", "ast": "assists"}
COUNTS = {"pts": "points", "reb": "reboundsTotal", "ast": "assists",
          "fga": "fieldGoalsAttempted", "fta": "freeThrowsAttempted",
          "fgm": "fieldGoalsMade", "fg3m": "threePointersMade"}
TRACK = {"chances": "reboundChancesTotal", "touches": "touches",
         "passes": "passes", "secondary": "secondaryAssists"}
KEYS = ["GAME_ID", "TEAM_ID", "PLAYER_ID"]
BASE_MIN = ["prior_mpg", "min3", "min5", "min10", "last_min", "started_last",
            "ewm_min", "days_rest", "home", "played3", "played10", "load3"]
ROLE = ["fast_min", "min_trend", "min_std", "missed_run", "team_games_seen",
        "changed_team", "games_since_move", "return_last", "season_gp", "career_gp",
        "team_expected_min", "teammate_expected_min", "teammate_pts36",
        "own_min_share", "candidate_count", "previous_absent_min"]
TRACK_FEATURES = [f"{k}36_{n}" for k in TRACK for n in (3, 10)] + [
    "tracking_games", "tracking_age", "reb_conversion", "ast_per_pass",
    "pts_per_touch", "reb_opportunity_rate", "ast_opportunity_rate",
    "pts_opportunity_rate", "chances_trend", "touches_trend", "passes_trend"]


def stat_features(stat):
    s = {"points": "pts", "reb": "reb", "ast": "ast"}[stat]
    return BASE_MIN + ["pred_min", f"prior_{s}36", f"{s}3", f"{s}10",
                       f"{s}36_10", f"ewm_{s}", "fga36_10", "fta36_10",
                       "ts10", "season_gp", "days_rest", "home"]



def _minutes_outcome(actual, *, future=False):
    """Emit upstream-normalized outcomes, never infer labels from legacy MIN.

    Comments are audit data, not DNP mappings. Emission and history ingestion
    share this normalization boundary.
    """
    fields = ("actual_row_status", "minutes_parse_status", "participation_evidence",
              "minutes_status", "minutes_value", "participated",
              "minutes_target_valid", "mapping_version")
    fallback = normalize_minutes(
        legacy_minutes=getattr(actual, "MIN", None),
        actual_row_present=actual is not None, completed=not future)
    out = {name: getattr(fallback, name) for name in fields}
    for name, default in (("evidence_refs", []),
                          ("minutes_raw_parse_statuses", []),
                          ("minutes_raw_evidence_json", None),
                          ("comment", None)):
        value = getattr(actual, name, default)
        out[name] = default if value is None or (
            not isinstance(value, (list, tuple, np.ndarray)) and pd.isna(value)
        ) else value
    out["minutes_raw"] = getattr(actual, "minutes", None)
    if actual is None or future:
        return out
    refs = out["evidence_refs"]
    version = getattr(actual, "mapping_version", None)
    status = getattr(actual, "minutes_status", None)
    # Arrow list columns return numpy arrays; a string is not a refs list.
    provenance = (isinstance(refs, (list, tuple, np.ndarray)) and len(refs) > 0
                  and all(isinstance(ref, str) and ref.strip() for ref in refs))
    recognized = (isinstance(version, str) and version == fallback.mapping_version
                  and isinstance(status, str)
                  and status in ("observed", "verified_dnp", "uncertain", "conflict")
                  and all(hasattr(actual, field) for field in fields))
    if not recognized or not provenance:
        return out
    for name in fields:
        out[name] = getattr(actual, name)
    out["actual_row_status"] = "present"
    value = out["minutes_value"]
    flag = out["minutes_target_valid"]
    numeric = (isinstance(value, (int, float, np.integer, np.floating))
               and not isinstance(value, (bool, np.bool_))
               and np.isfinite(value) and value >= 0)
    valid = (isinstance(flag, (bool, np.bool_)) and bool(flag)
             and status in ("observed", "verified_dnp") and numeric)
    participation = out["participation_evidence"]
    if status == "verified_dnp":
        valid = (valid and value == 0 and isinstance(participation, str)
                 and participation == "dnp")
    if isinstance(participation, str) and participation == "conflict":
        out["minutes_status"] = "conflict"
        valid = False
    if not valid and out["minutes_status"] in ("observed", "verified_dnp"):
        out.update(minutes_status="uncertain", participated=None)
    if out["minutes_status"] == "conflict":
        out["participated"] = None
    out["minutes_target_valid"] = bool(valid)
    out["minutes_value"] = float(value) if valid else None
    return out


def _history_record(actual, game, team, day):
    """One completed chronological slot; absence is not an actual appearance."""
    rec = _minutes_outcome(actual)
    participation = rec["participated"]
    rec["participated"] = (bool(participation)
                           if isinstance(participation, (bool, np.bool_)) else None)
    rec.update(min=rec["minutes_value"], date=day, team=team,
               GAME_ID=game.GAME_ID,
               started=int(bool(getattr(actual, "position", "") or ""))
               if actual is not None and pd.notna(getattr(actual, "position", None)) else 0)
    for key, source in {**COUNTS, **TRACK}.items():
        value = getattr(actual, source, None)
        rec[key] = float(value) if value is not None and pd.notna(value) else np.nan
    return rec


def _played_minutes(rec):
    return rec["minutes_target_valid"] and rec["participated"] is True


def _missed_run(history):
    count = 0
    for rec in reversed(history):
        if rec["participated"] is None:
            return np.nan
        if rec["participated"]:
            return count
        count += 1
    return count


def build_features(pg, games, *, future_rosters=None, emit_game_ids=None):
    """Optional future_rosters[(game_id, team_id)] supplies observed live rosters.

    Future games have no targets and never update history. emit_game_ids skips
    historical feature calculation while replaying the identical history state.
    Minutes labels consume upstream validity; legacy labels remain unknown.
    minN is played-only; all_minN, minute EWM/fast and prior_mpg use all valid
    minute outcomes (including evidenced zeros). Empty observed windows are
    null; EWM/prior cold starts retain explicit priors with zero valid counts.
    Window counts describe chronological slots before eligibility filtering.
    EWM completeness counts cover all actual rows since replay began; fast_min
    uses the same minute eligibility. Counts/rates require paired finite stats.
    """
    future_rosters = future_rosters or {}
    future_ids = {key[0] for key in future_rosters}
    if future_ids & set(pg.GAME_ID):
        raise ValueError("future games already have outcomes in history")
    pg = pg[pg.SEASON_TYPE == "Regular Season"].copy()
    games = games[games.SEASON_TYPE == "Regular Season"].sort_values(["GAME_DATE", "GAME_ID"])
    if pg.duplicated(KEYS).any():
        raise ValueError("duplicate player-game keys")
    boxes = {gid: d for gid, d in pg.groupby("GAME_ID", sort=False)}
    hist = defaultdict(lambda: deque(maxlen=10))
    team_hist = defaultdict(lambda: deque(maxlen=10))
    roster, last_team, last_date, ewm, fast = {}, {}, {}, {}, {}
    # These totals are accumulated after games; prior() reads strictly older seasons.
    totals = defaultdict(lambda: defaultdict(float))
    league = defaultdict(lambda: defaultdict(float))
    season_gp, career_gp, tenure = defaultdict(int), defaultdict(int), defaultdict(int)
    completeness = defaultdict(lambda: defaultdict(int))
    prior_cache = {}
    rows = []

    def prior(pid, season):
        key = (pid, season)
        if key in prior_cache:
            return prior_cache[key]
        old = sorted(s for s in league if s < season)
        weights = list(zip(reversed(old[-3:]), (5., 4., 3.)))
        den = sum(w * totals[(pid, s)]["min"] for s, w in weights)
        result = {}
        for k, default in (("pts", 16.), ("reb", 7.), ("ast", 3.)):
            lgmin = sum(league[s][f"{k}_min"] for s in old)
            mean = 36 * sum(league[s][k] for s in old) / lgmin if lgmin else default
            num = 36 * sum(w * totals[(pid, s)][k] for s, w in weights)
            paired_den = sum(w * totals[(pid, s)][f"{k}_min"] for s, w in weights)
            result[f"prior_{k}36"] = (num + 1000 * mean) / (paired_den + 1000)
            result[f"prior_{k}_eligible"] = sum(totals[(pid, s)][f"{k}_eligible"] for s, _ in weights)
        gp = sum(w * totals[(pid, s)]["gp"] for s, w in weights)
        result["prior_mpg"] = den / gp if gp else 12.
        result["prior_minutes_valid"] = sum(totals[(pid, s)]["gp"] for s, _ in weights)
        result["prior_slots"] = sum(totals[(pid, s)]["slots"] for s, _ in weights)
        prior_cache[key] = result
        return result

    def features(pid, tid, season, day):
        h = list(hist[pid]); th = list(team_hist[(tid, pid)])
        played = [x for x in h if _played_minutes(x)]
        m = [x["min"] for x in played]
        pr = prior(pid, season)
        mean = lambda a: float(np.mean(a)) if len(a) else np.nan
        r = dict(pr)
        for n in (3, 5, 10):
            window = h[-n:]
            eligible = [x for x in window if _played_minutes(x)]
            r[f"min{n}"] = mean([x["min"] for x in eligible])
            r[f"min{n}_slots"] = len(window)
            r[f"min{n}_eligible"] = len(eligible)
            valid = [x for x in window if x["minutes_target_valid"]]
            r[f"all_min{n}"] = mean([x["min"] for x in valid])
            r[f"all_min{n}_valid"] = len(valid)
        for n in (3, 10):
            window = th[-n:]
            known = [x for x in window if x["participated"] is not None]
            r[f"played{n}"] = mean([x["participated"] for x in known])
            r[f"played{n}_slots"] = len(window)
            r[f"played{n}_known"] = len(known)
        load = [x for x in h if 0 < (day-x["date"]).days <= 3]
        load_valid = [x for x in load if x["minutes_target_valid"]]
        transition = [x["participated"] for x in th[-2:]]
        r.update(last_min=th[-1]["min"] if th else np.nan,
                 last_participated=th[-1]["participated"] if th else None,
                 started_last=th[-1]["started"] if th else 0,
                 ewm_min=ewm.get(pid, {}).get("min", pr["prior_mpg"]),
                 fast_min=fast.get(pid, pr["prior_mpg"]),
                 ewm_min_slots=completeness[pid]["slots"],
                 ewm_min_valid=completeness[pid]["min"],
                 days_rest=min((day-last_date[pid]).days, 120) if pid in last_date else 120,
                 load3=sum(x["min"] for x in load_valid) if load_valid else np.nan,
                 load3_slots=len(load), load3_valid=len(load_valid),
                 min_std=float(np.std(m)) if m else np.nan,
                 team_games_seen=len(th), games_since_move=tenure[(tid,pid)],
                 changed_team=int(bool(h) and any(x["team"] != tid for x in h)),
                 return_last=int(transition == [False, True])
                 if len(transition) == 2 and None not in transition else np.nan,
                 season_gp=season_gp[(season,pid)], career_gp=career_gp[pid])
        r["min_trend"] = r["fast_min"] - r["min10"]
        r["missed_run"] = _missed_run(th)
        for k in COUNTS:
            for n in (3, 10):
                recent = [x for x in h[-n:] if _played_minutes(x) and np.isfinite(x[k])]
                mins = sum(x["min"] for x in recent)
                r[f"{k}{n}"] = mean([x[k] for x in recent])
                r[f"{k}{n}_eligible"] = len(recent)
                r[f"{k}36_{n}"] = 36 * sum(x[k] for x in recent)/mins if mins else np.nan
            r[f"ewm_{k}"] = ewm.get(pid, {}).get(k, r[f"{k}10"])
            r[f"ewm_{k}_valid"] = completeness[pid][k]
        shooting = [x for x in played if all(np.isfinite(x[k]) for k in ("fga","fta","pts"))]
        fga, fta, pts = (sum(x[k] for x in shooting) for k in ("fga","fta","pts"))
        r["ts10"] = pts/(2*(fga+.44*fta)) if fga+.44*fta else np.nan
        r["ts10_eligible"] = len(shooting)
        tracked = lambda x: _played_minutes(x) and np.isfinite(x["touches"]) and x["touches"] > 0
        valid = [x for x in h if tracked(x)]
        r["tracking_games"] = len(valid)
        r["tracking_age"] = min((day-valid[-1]["date"]).days,120) if valid else 120
        for k in TRACK:
            for n in (3,10):
                v = [x for x in h[-n:] if tracked(x) and np.isfinite(x[k])]
                mins = sum(x["min"] for x in v)
                r[f"{k}36_{n}"] = 36*sum(x[k] for x in v)/mins if mins else np.nan
                r[f"{k}{n}_eligible"] = len(v)
        # Conversion numerator, opportunity and minutes use identical records.
        for target, opportunity, name in (("reb","chances","reb_conversion"),
                                          ("ast","passes","ast_per_pass"),
                                          ("pts","touches","pts_per_touch")):
            v = [x for x in valid if np.isfinite(x[opportunity]) and np.isfinite(x[target])]
            den = sum(x[opportunity] for x in v)
            mins = sum(x["min"] for x in v)
            rate = 36 * den/mins if mins else np.nan
            default = pr[f"prior_{target}36"]/max(rate,1.) if np.isfinite(rate) else 0.
            pseudo = 200 * den/mins if mins else 0.
            conv = (sum(x[target] for x in v)+pseudo*default)/(den+pseudo) if den+pseudo else np.nan
            r[name] = conv
            r[f"{name}_eligible"] = len(v)
            r[f"{target}_opportunity_rate"] = r[f"{opportunity}36_3"] * conv
            r[f"{opportunity}_trend"] = r[f"{opportunity}36_3"]-r[f"{opportunity}36_10"]
        return r

    for day, gg in games.groupby("GAME_DATE", sort=True):
        pending = []
        for g in gg.itertuples():
            future = g.GAME_ID in future_ids
            if g.GAME_ID not in boxes and not future:
                raise ValueError(f"missing box score {g.GAME_ID}")
            box = boxes.get(g.GAME_ID)
            for tid in (g.HOME_TEAM_ID, g.AWAY_TEAM_ID):
                actual = {} if future else {int(r.PLAYER_ID):r for r in box[box.TEAM_ID == tid].itertuples()}
                if future:
                    if (g.GAME_ID,tid) not in future_rosters:
                        raise ValueError("both future rosters are required")
                    candidates = sorted(set(future_rosters[(g.GAME_ID,tid)]))
                else:
                    candidates = sorted(pid for pid in roster.get(tid,set()) if last_team.get(pid) == tid)
                if emit_game_ids is not None and g.GAME_ID not in emit_game_ids:
                    if not future:
                        pending.append((g,tid,actual,candidates))
                    continue
                fs = {pid:features(pid,tid,g.SEASON,day) for pid in candidates}
                total = sum(x["ewm_min"] for x in fs.values())
                ptsvol = sum(x["prior_pts36"]*x["ewm_min"] for x in fs.values())
                absent = sum(x["prior_mpg"] for x in fs.values() if x["last_participated"] is False)
                for pid in sorted(set(candidates)|set(actual)):
                    r = fs.get(pid, {})
                    if pid in fs:
                        own = r["ewm_min"]
                        r.update(team_expected_min=total, teammate_expected_min=total-own,
                                 teammate_pts36=(ptsvol-r["prior_pts36"]*own)/max(total-own,1.),
                                 own_min_share=own/max(total,1.), candidate_count=len(candidates),
                                 previous_absent_min=absent)
                    a = actual.get(pid)
                    row = dict(r, GAME_ID=g.GAME_ID, GAME_DATE=day, SEASON=g.SEASON,
                               TEAM_ID=tid, PLAYER_ID=pid, forecastable=pid in fs,
                               home=int(tid == g.HOME_TEAM_ID))
                    row.update(_minutes_outcome(a, future=future))
                    row["minutes"] = row["minutes_value"]
                    for target, source in TARGETS.items():
                        if target == "minutes":
                            continue
                        value = getattr(a,source,0.) if a is not None else 0.
                        row[target] = np.nan if future else (float(value) if pd.notna(value) else 0.)
                    rows.append(row)
                if not future:
                    pending.append((g,tid,actual,candidates))
        # Update only after EVERY game on this date has been forecast.
        for g,tid,actual,candidates in pending:
            roster[tid] = set(actual)
            for pid in set(actual)|set(candidates):
                a = actual.get(pid)
                rec = _history_record(a, g, tid, day)
                mn = rec["min"]
                team_hist[(tid,pid)].append(rec)
                if a is None:
                    continue
                if last_team.get(pid) != tid:
                    tenure[(tid,pid)] = 0
                tenure[(tid,pid)] += 1
                last_team[pid] = tid
                hist[pid].append(rec)
                completeness[pid]["slots"] += 1
                for accumulator in (totals[(pid,g.SEASON)],league[g.SEASON]):
                    accumulator["slots"] += 1
                if rec["participated"] is True:
                    last_date[pid] = day
                    season_gp[(g.SEASON,pid)] += 1; career_gp[pid] += 1
                if rec["minutes_target_valid"]:
                    completeness[pid]["min"] += 1
                    e = ewm.setdefault(pid,{})
                    e["min"] = .25*mn+.75*e["min"] if "min" in e else mn
                    fast[pid] = .65*mn+.35*fast.get(pid,mn)
                    # All-outcome minute accumulators include evidenced zeros.
                    for accumulator in (totals[(pid,g.SEASON)],league[g.SEASON]):
                        accumulator["min"] += mn; accumulator["gp"] += 1
                    for k in COUNTS:
                        if not _played_minutes(rec) or not np.isfinite(rec[k]):
                            continue
                        completeness[pid][k] += 1
                        e[k] = .15*rec[k]+.85*e[k] if k in e else rec[k]
                        for accumulator in (totals[(pid,g.SEASON)],league[g.SEASON]):
                            accumulator[k] += rec[k]
                            accumulator[f"{k}_min"] += mn
                            accumulator[f"{k}_eligible"] += 1
    result = pd.DataFrame(rows).sort_values(["GAME_DATE",*KEYS]).reset_index(drop=True)
    for column in ("minutes", "minutes_value"):
        result[column] = pd.array(result[column], dtype="Float64")
    for column in ("participated", "minutes_target_valid"):
        result[column] = pd.array(result[column], dtype="boolean")
    return result


def allocate_minutes(pred, teams, cap=48.):
    """Proportional allocation, with a per-player cap and an explicit short-roster ceiling."""
    result = np.zeros(len(pred))
    for indices in teams:
        x = np.maximum(np.asarray(pred)[indices], .001)
        target = min(240.,cap*len(x))
        active = np.ones(len(x),dtype=bool); y = np.zeros(len(x))
        left = target
        while active.any():
            proposal = left*x[active]/x[active].sum()
            saturated = proposal > cap
            ix = np.flatnonzero(active)
            if not saturated.any():
                y[ix] = proposal; break
            y[ix[saturated]] = cap
            active[ix[saturated]] = False
            left -= cap*saturated.sum()
        result[indices] = y
    return result
