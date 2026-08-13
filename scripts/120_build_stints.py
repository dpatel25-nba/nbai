"""
Stint reconstruction — the keystone for the game simulator.

Rebuilds, for every moment of every game, WHICH 5 PLAYERS ARE ON THE COURT for
each team. Everything downstream needs this: the rotation engine (what a real
substitution pattern looks like), the assignment model (who can guard whom),
RAPM/lineup ratings, and the possession simulator's state.

Two feed quirks make this harder than it looks, and both are handled here:

 1. ACTION ORDER IS NOT GAME ORDER. `actionNumber` is the feed's entry order and
    the NBA appends corrections after the fact — we observe a 2:00 turnover filed
    after the "End of 1st Period" marker, and 3:26 substitutions wedged between
    0:45 and 0:37. The turnover sequence tags (P1.T4 landing after P1.T5) prove
    the clock is the truth, so events are sorted by clock, actionNumber breaking ties.

 2. PERIOD-START LINEUPS ARE NOT IN THE FEED. Halftime and quarter-break changes
    are simply not recorded: across 1,315 games there are 6 substitutions at the
    top of period 3 versus 15,518 within it. Players just reappear. So a lineup
    carried across a period break is wrong, and each period's opening five must be
    inferred independently.

    Inference rule: walk the period in order; a player reveals himself as having
    STARTED it if he acts, or is substituted OUT, before ever being substituted
    IN. The first five such players per team are that period's starters. Period 1
    is pinned exactly by the box score (`position` is non-empty only for starters).
    Short falls are filled from the prior period's closing lineup, then by minutes.

A substitution row's `personId` is the player going OUT; the player coming IN
appears only in the description ("SUB: Eason FOR Smith Jr."), so he is resolved
by name against that game's roster.

Output: data/parquet/stints/<season>.parquet — one row per stint (a contiguous
span with an unchanged 10-man set), with both lineups, duration, points and
possessions for each side.

Validation: every player's summed stint time is compared to his box-score
minutes. That reconciliation is the acid test — if lineups are wrong, it fails.

Usage:
  python scripts/120_build_stints.py --season 2025-26
  python scripts/120_build_stints.py                  # all seasons
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PBP_DIR = ROOT / "data" / "parquet" / "pbp"
PG = ROOT / "data" / "parquet" / "player_games.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT_DIR = ROOT / "data" / "parquet" / "stints"

SUB_RE = re.compile(r"SUB:\s*(.+?)\s+FOR\s+(.+?)\s*$", re.I)
# rows that are markers, not player actions
NON_ACTION = {"period", "Timeout", "Instant Replay", "Jump Ball", "Ejection", ""}


def plen(period: int) -> float:
    return 720.0 if period <= 4 else 300.0


def elapsed(period: int, sec_remaining: float) -> float:
    """Seconds elapsed since tip-off."""
    if period <= 4:
        return (period - 1) * 720.0 + (720.0 - sec_remaining)
    return 2880.0 + (period - 5) * 300.0 + (300.0 - sec_remaining)


def norm_name(s) -> str:
    """Fold to plain ASCII: the pbp writes 'Jokic'/'Doncic'/'Schroder' while the
    box score carries the accents ('Jokić', 'Dončić', 'Schröder')."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = s.lower().strip().replace(".", "").replace("'", "").replace("-", " ")
    return " ".join(s.split())


class RosterIndex:
    """Resolve a pbp display name to a PLAYER_ID within one team-game."""

    def __init__(self, players: list[dict]):
        self.by_key: dict[str, list[int]] = defaultdict(list)
        for p in players:
            pid, fn, ln = p["PLAYER_ID"], p["firstName"] or "", p["familyName"] or ""
            for key in (ln, f"{fn} {ln}", f"{fn[:1]} {ln}" if fn else ln):
                self.by_key[norm_name(key)].append(pid)

    def resolve(self, name, exclude: set, prefer: set):
        cands = list(dict.fromkeys(self.by_key.get(norm_name(name), [])))
        if not cands:
            return None
        if len(cands) > 1:  # ambiguous surname: prefer an off-court player who played
            cands.sort(key=lambda p: (p in exclude, p not in prefer))
        return cands[0]


def parse_sub(e, idx: RosterIndex, on: set, played: set):
    """-> (in_pid, out_pid) for a substitution row."""
    m = SUB_RE.search(str(e.DESCRIPTION or ""))
    out_pid = None if pd.isna(e.PLAYER_ID) else int(e.PLAYER_ID)
    in_pid = idx.resolve(m.group(1), on, played) if m else None
    if out_pid is None and m:
        out_pid = idx.resolve(m.group(2), set(), played)
    return in_pid, out_pid


def infer_period_starters(pev, tid, idx, played, prev_lineup, box_min):
    """Who was on the floor for `tid` when this period tipped off."""
    entered: set[int] = set()
    start: list[int] = []

    def reveal(pid):
        if pid is not None and pid not in entered and pid not in start:
            start.append(pid)

    for e in pev.itertuples():
        if len(start) >= 5:
            break
        if e.TEAM_ID != tid:
            continue
        if e.ACTION_TYPE == "Substitution":
            in_pid, out_pid = parse_sub(e, idx, set(), played)
            reveal(out_pid)
            if in_pid is not None:
                entered.add(in_pid)
        elif e.ACTION_TYPE not in NON_ACTION and not pd.isna(e.PLAYER_ID):
            pid = int(e.PLAYER_ID)
            if pid in played:
                reveal(pid)

    if len(start) < 5:  # silent players: fall back to who closed the prior period
        for pid in sorted(prev_lineup, key=lambda p: -box_min.get(p, 0)):
            if len(start) >= 5:
                break
            if pid not in entered and pid not in start:
                start.append(pid)
    if len(start) < 5:  # last resort: the highest-minute players still unplaced
        for pid in sorted(played, key=lambda p: -box_min.get(p, 0)):
            if len(start) >= 5:
                break
            if pid not in entered and pid not in start:
                start.append(pid)
    return set(start[:5])


def build_game(ev, meta, idx: dict, box_starters: dict, played: dict, box_min: dict):
    """-> (stint rows, per-player on-court seconds, diagnostics)."""
    home, away = meta.HOME_TEAM_ID, meta.AWAY_TEAM_ID
    teams = (home, away)
    rows, secs = [], defaultdict(float)
    diag = {"repairs": 0, "unresolved": 0, "bad_lineup_sec": 0.0, "filled": 0}
    prev_lineup = {t: set() for t in teams}
    # Running score, game-level. SCORE_HOME/AWAY are only populated on scoring
    # rows (74% are NaN), so the last known value must be CARRIED FORWARD rather
    # than treated as zero — and it has to persist across period boundaries.
    base = last = (0.0, 0.0)   # base = score when the current stint began

    for period, pev in ev.groupby("PERIOD", sort=True):
        on = {}
        for t in teams:
            if period == 1 and len(box_starters.get(t, set())) == 5:
                on[t] = set(box_starters[t])          # exact, from the box score
            else:
                on[t] = infer_period_starters(pev, t, idx[t], played.get(t, set()),
                                              prev_lineup[t], box_min)
                diag["filled"] += 1
        cur = elapsed(period, plen(period))
        poss = {t: 0 for t in teams}

        def close(t_end):
            nonlocal cur, poss, base
            dur = t_end - cur
            if dur <= 0:
                cur = max(cur, t_end)
                return
            sc = (last[0] - base[0], last[1] - base[1])
            if all(len(on[t]) == 5 for t in teams):
                for t in teams:
                    for pid in on[t]:
                        secs[pid] += dur
                rows.append({
                    "GAME_ID": meta.GAME_ID, "SEASON": meta.SEASON,
                    "SEASON_TYPE": meta.SEASON_TYPE, "PERIOD": period,
                    "START_SEC": cur, "END_SEC": t_end, "DUR_SEC": dur,
                    "HOME_TEAM_ID": home, "AWAY_TEAM_ID": away,
                    "HOME_LINEUP": sorted(on[home]), "AWAY_LINEUP": sorted(on[away]),
                    "HOME_PTS": sc[0], "AWAY_PTS": sc[1],
                    "HOME_POSS": poss[home], "AWAY_POSS": poss[away]})
            else:
                diag["bad_lineup_sec"] += dur
            cur = t_end
            base = last
            poss = {t: 0 for t in teams}

        for e in pev.itertuples():
            t_now = elapsed(period, e.SEC_REMAINING if not pd.isna(e.SEC_REMAINING) else 0.0)
            if not pd.isna(e.SCORE_HOME) and not pd.isna(e.SCORE_AWAY):
                last = (e.SCORE_HOME, e.SCORE_AWAY)
            tid = None if pd.isna(e.TEAM_ID) else e.TEAM_ID

            if e.ACTION_TYPE == "Substitution" and tid in on:
                close(t_now)
                in_pid, out_pid = parse_sub(e, idx[tid], on[tid], played.get(tid, set()))
                if in_pid is None:
                    diag["unresolved"] += 1
                if out_pid in on[tid]:
                    on[tid].discard(out_pid)
                if in_pid is not None:
                    on[tid].add(in_pid)
                continue

            if tid in poss and e.ACTION_TYPE in ("Made Shot", "Missed Shot", "Turnover"):
                poss[tid] += 1

            # safety net: someone acted while we think he is on the bench
            if (tid in on and e.ACTION_TYPE not in NON_ACTION and not pd.isna(e.PLAYER_ID)):
                pid = int(e.PLAYER_ID)
                if pid in played.get(tid, set()) and pid not in on[tid] and len(on[tid]) < 5:
                    on[tid].add(pid)
                    diag["repairs"] += 1

        close(elapsed(period, 0.0))
        prev_lineup = {t: set(on[t]) for t in teams}

    return rows, secs, diag


def run_season(season, games, pg):
    path = PBP_DIR / f"{season}.parquet"
    if not path.exists():
        return None
    pbp = pd.read_parquet(path, columns=[
        "GAME_ID", "PERIOD", "ACTION_NUMBER", "SEC_REMAINING", "TEAM_ID",
        "PLAYER_ID", "ACTION_TYPE", "DESCRIPTION", "SCORE_HOME", "SCORE_AWAY"])
    pbp = pbp.sort_values(["GAME_ID", "PERIOD", "SEC_REMAINING", "ACTION_NUMBER"],
                          ascending=[True, True, False, True])

    gmeta = {r.GAME_ID: r for r in games[games.SEASON == season].itertuples()}
    sp = pg[pg.SEASON == season]
    roster, starters, played, box_min = (defaultdict(dict), defaultdict(dict),
                                         defaultdict(dict), {})
    for r in sp.itertuples():
        roster[r.GAME_ID].setdefault(r.TEAM_ID, []).append(
            {"PLAYER_ID": r.PLAYER_ID, "firstName": r.firstName, "familyName": r.familyName})
        if r.MIN and r.MIN > 0:
            played[r.GAME_ID].setdefault(r.TEAM_ID, set()).add(r.PLAYER_ID)
            box_min[(r.GAME_ID, r.PLAYER_ID)] = r.MIN
        if isinstance(r.position, str) and r.position.strip():
            starters[r.GAME_ID].setdefault(r.TEAM_ID, set()).add(r.PLAYER_ID)

    all_rows, diffs = [], []
    agg = defaultdict(float)
    for gid, ev in pbp.groupby("GAME_ID", sort=False):
        meta = gmeta.get(gid)
        if meta is None or gid not in roster:
            continue
        idx = {t: RosterIndex(pl) for t, pl in roster[gid].items()}
        if meta.HOME_TEAM_ID not in idx or meta.AWAY_TEAM_ID not in idx:
            continue
        bm = {p: m for (g, p), m in box_min.items() if g == gid}
        rows, secs, diag = build_game(ev, meta, idx, starters.get(gid, {}),
                                      played.get(gid, {}), bm)
        all_rows.extend(rows)
        for k, v in diag.items():
            agg[k] += v
        for pid, s in secs.items():
            if (gid, pid) in box_min:
                diffs.append(abs(s / 60.0 - box_min[(gid, pid)]))

    if not all_rows:
        return None
    df = pd.DataFrame(all_rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_DIR / f"{season}.parquet", index=False)
    d = np.array(diffs)
    return {"season": season, "games": df.GAME_ID.nunique(), "stints": len(df),
            "mae": d.mean(), "p95": np.percentile(d, 95),
            "w1": (d <= 1).mean(), "w3": (d <= 3).mean(), **agg}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default=None)
    args = ap.parse_args()
    games = pd.read_parquet(GAMES)
    pg = pd.read_parquet(PG, columns=["GAME_ID", "SEASON", "TEAM_ID", "PLAYER_ID",
                                      "firstName", "familyName", "position", "MIN"])
    seasons = [args.season] if args.season else sorted(p.stem for p in PBP_DIR.glob("*.parquet"))
    print(f"{'season':<10}{'games':>7}{'stints':>9}{'minMAE':>9}{'p95':>7}"
          f"{'<=1m':>8}{'<=3m':>8}{'repair':>8}{'unres':>7}{'badsec':>9}")
    for s in seasons:
        r = run_season(s, games, pg)
        if r:
            print(f"{r['season']:<10}{r['games']:>7,}{r['stints']:>9,}{r['mae']:>9.3f}"
                  f"{r['p95']:>7.2f}{r['w1']*100:>7.1f}%{r['w3']*100:>7.1f}%"
                  f"{int(r['repairs']):>8,}{int(r['unresolved']):>7,}{r['bad_lineup_sec']:>9,.0f}")
    print(f"\nWrote {OUT_DIR}/")


if __name__ == "__main__":
    main()
