"""
Broad anomaly audit of the game-level data (game_logs + games tables).

Goes beyond "does it parse" to hunt semantic quirks like the neutral-site
both-teams-'@' case: matchup inconsistencies, duplicates, self-games, score /
minute outliers, schedule-count oddities, date/season mismatches, and unstable
team identifiers. Prints a FLAG/OK verdict per check with examples.

Usage: python scripts/12_audit_games.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "data" / "parquet" / "game_logs.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"

# season -> acceptable (low, high) regular-season games per team.
# COVID years differ: 2019-20 was suspended mid-season with bubble seeding games
# (teams landed on 64-75); 2020-21 was a shortened 72-game season. Others: 82.
EXPECTED_RS_GPT = {"2019-20": (64, 75), "2020-21": (72, 72)}  # default (81, 82)
flags = 0


def report(name: str, bad: pd.DataFrame | list, show=6, fmt=None) -> None:
    global flags
    n = len(bad)
    print(f"[{'FLAG' if n else 'OK':>4}] {name}" + (f"  ({n})" if n else ""))
    if n:
        flags += 1
        rows = bad if isinstance(bad, list) else bad.head(show).to_dict("records")
        for r in rows[:show]:
            print("        ", fmt(r) if fmt else r)
        if n > show:
            print(f"         ... +{n - show} more")


def main() -> None:
    df = pd.read_parquet(LOGS)
    g = pd.read_parquet(GAMES)

    # tokens from MATCHUP: "ABC vs. XYZ" / "ABC @ XYZ"
    parts = df["MATCHUP"].str.split(r" vs\. | @ ", regex=True, expand=True)
    df["SELF_ABBR"], df["OPP_ABBR"] = parts[0], parts[1]
    df["IS_HOME"] = df["MATCHUP"].str.contains("vs.", regex=False)

    print("=== STRUCTURE / HOME-AWAY ===")
    # 1. home rows per game != 1  (neutral both-'@', or both 'vs.')
    hc = df.groupby("GAME_ID")["IS_HOME"].sum()
    both_away = hc[hc == 0]
    both_home = hc[hc == 2]
    report("games with 0 home rows (both '@', neutral-site)", list(both_away.index),
           fmt=lambda x: x)
    report("games with 2 home rows (both 'vs.')", list(both_home.index), fmt=lambda x: x)

    # 2. self abbreviation should equal the row's own team abbreviation
    bad_self = df[df["SELF_ABBR"] != df["TEAM_ABBREVIATION"]]
    report("MATCHUP self-token != TEAM_ABBREVIATION", bad_self,
           fmt=lambda r: f"{r['GAME_ID']} {r['TEAM_ABBREVIATION']} vs token {r['SELF_ABBR']}")

    # 3. the two teams in a game must name each other
    opp_ok = df.groupby("GAME_ID").agg(
        selfs=("SELF_ABBR", lambda s: sorted(s)),
        opps=("OPP_ABBR", lambda s: sorted(s)))
    mismatch = opp_ok[opp_ok["selfs"] != opp_ok["opps"]]
    report("teams don't name each other in MATCHUP", list(mismatch.index), fmt=lambda x: x)

    # 4. team plays itself
    report("team plays itself (HOME_ID == AWAY_ID)",
           g[g["HOME_TEAM_ID"] == g["AWAY_TEAM_ID"]],
           fmt=lambda r: r["GAME_ID"])

    print("\n=== DUPLICATES ===")
    rpg = df.groupby("GAME_ID").size()
    report("GAME_ID without exactly 2 rows", list(rpg[rpg != 2].index), fmt=lambda x: x)
    dup_team = df[df.duplicated(["GAME_ID", "TEAM_ID"], keep=False)]
    report("duplicate (GAME_ID, TEAM_ID) rows", dup_team,
           fmt=lambda r: f"{r['GAME_ID']} {r['TEAM_ABBREVIATION']}")
    dup_match = g[g.duplicated(["GAME_DATE", "HOME_TEAM_ID", "AWAY_TEAM_ID"], keep=False)]
    report("same date+home+away appears twice", dup_match,
           fmt=lambda r: f"{r['GAME_DATE'].date()} {r['HOME_TEAM']} v {r['AWAY_TEAM']}")

    print("\n=== SCORES / MINUTES ===")
    report("null or zero points", g[(g["HOME_PTS"] <= 0) | (g["AWAY_PTS"] <= 0)],
           fmt=lambda r: r["GAME_ID"])
    report("ties (margin == 0)", g[g["MARGIN"] == 0], fmt=lambda r: r["GAME_ID"])
    outlier = g[(g[["HOME_PTS", "AWAY_PTS"]].min(axis=1) < 60) |
                (g[["HOME_PTS", "AWAY_PTS"]].max(axis=1) > 175)]
    report("team score outlier (<60 or >175)", outlier,
           fmt=lambda r: f"{r['GAME_ID']} {r['HOME_TEAM']} {r['HOME_PTS']}-{r['AWAY_PTS']} {r['AWAY_TEAM']}")
    # team minutes should match between the two teams (both play the same length)
    mins = df.groupby("GAME_ID")["MIN"].nunique()
    report("home/away team minutes disagree", list(mins[mins != 1].index), fmt=lambda x: x)

    print("\n=== SCHEDULE / COUNTS ===")
    bad_type = df[~df["SEASON_TYPE"].isin(["Regular Season", "Playoffs"])]
    report("unexpected SEASON_TYPE", bad_type, fmt=lambda r: r["SEASON_TYPE"])
    # teams per season
    tps = df.groupby("SEASON")["TEAM_ID"].nunique()
    report("season without 30 teams", list(tps[tps != 30].items()),
           fmt=lambda x: f"{x[0]}: {x[1]} teams")
    # regular-season games per team per season vs expected
    rs = df[df["SEASON_TYPE"] == "Regular Season"]
    gpt = rs.groupby(["SEASON", "TEAM_ABBREVIATION"]).size().reset_index(name="G")
    odd = []
    for _, r in gpt.iterrows():
        lo, hi = EXPECTED_RS_GPT.get(r["SEASON"], (81, 82))
        if not (lo <= r["G"] <= hi):
            odd.append(f"{r['SEASON']} {r['TEAM_ABBREVIATION']}: {r['G']} (exp {lo}-{hi})")
    report("team regular-season game count out of range", odd, fmt=lambda x: x)

    print("\n=== DATES / IDENTIFIERS ===")
    report("null game date", g[g["GAME_DATE"].isna()], fmt=lambda r: r["GAME_ID"])
    # game date year should be within the season span (start year or +1)
    g2 = g.copy()
    g2["START_YR"] = g2["SEASON"].str[:4].astype(int)
    g2["YR"] = g2["GAME_DATE"].dt.year
    off_date = g2[(g2["YR"] < g2["START_YR"]) | (g2["YR"] > g2["START_YR"] + 1)]
    report("game date outside its season span", off_date,
           fmt=lambda r: f"{r['GAME_ID']} {r['SEASON']} on {r['GAME_DATE'].date()}")
    # team ids mapping to multiple abbreviations (relocations/rebrands — informational)
    id_abbr = df.groupby("TEAM_ID")["TEAM_ABBREVIATION"].unique()
    multi = id_abbr[id_abbr.apply(len) > 1]
    print(f"[INFO] team IDs with multiple abbreviations (rebrands): {len(multi)}")
    for tid, abbrs in multi.items():
        print(f"         {tid}: {list(abbrs)}")

    print("\n" + "=" * 55)
    print("AUDIT CLEAN — no anomalies beyond known/benign ones."
          if flags == 0 else f"{flags} check(s) flagged — review above.")


if __name__ == "__main__":
    main()
