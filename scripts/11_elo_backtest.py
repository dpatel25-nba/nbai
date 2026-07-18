"""
Stage 1: FiveThirtyEight-style Elo baseline + leakage-free walk-forward backtest.

Elo is inherently online/walk-forward: each game is predicted using ONLY ratings
built from prior games, then ratings update on the result. So there is no leakage
by construction — the pre-game probability is a true out-of-sample forecast.

Parameters (from docs/MODELING_PLAN.md, FiveThirtyEight NBA Elo):
  K = 20, base rating 1500, home-court = 100 Elo (0 at neutral sites),
  400-point logistic scale, MOV multiplier ((MOV+3)^0.8)/(7.5+0.006*dElo),
  between-season reversion: R <- 0.75*R + 0.25*1500.

Evaluation: skip 2013-14 as burn-in (ratings warming up), report calibration-aware
metrics (accuracy, log-loss, Brier) overall, per-season, and a calibration table,
each vs. naive baselines. Also saves pre-game Elo features for the later ML stage.
"""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GAMES = ROOT / "data" / "parquet" / "games.parquet"
FEAT_DIR = ROOT / "data" / "features"
FEAT_DIR.mkdir(parents=True, exist_ok=True)

K = 20.0
BASE = 1500.0
HCA = 100.0            # Elo points, home-court advantage
SCALE = 400.0
REVERT = 0.25          # fraction reverted toward mean between seasons
BURN_IN_SEASON = "2013-14"


def win_prob(elo_home_adj: float, elo_away: float) -> float:
    """Logistic win probability for the home team (home adjustment already applied)."""
    return 1.0 / (1.0 + 10 ** (-(elo_home_adj - elo_away) / SCALE))


def mov_multiplier(margin: float, elo_diff_winner: float) -> float:
    """538 margin-of-victory multiplier; elo_diff_winner = winner - loser pre-game."""
    return ((abs(margin) + 3.0) ** 0.8) / (7.5 + 0.006 * elo_diff_winner)


def run_elo(games: pd.DataFrame) -> pd.DataFrame:
    ratings: dict[int, float] = defaultdict(lambda: BASE)
    prev_season = None
    rows = []

    for g in games.itertuples():
        # between-season reversion toward the mean
        if prev_season is not None and g.SEASON != prev_season:
            for t in list(ratings):
                ratings[t] = (1 - REVERT) * ratings[t] + REVERT * BASE
        prev_season = g.SEASON

        rh, ra = ratings[g.HOME_TEAM_ID], ratings[g.AWAY_TEAM_ID]
        hca = 0.0 if g.NEUTRAL else HCA
        p_home = win_prob(rh + hca, ra)

        rows.append((g.GAME_ID, g.GAME_DATE, g.SEASON, g.SEASON_TYPE,
                     rh, ra, p_home, g.HOME_WIN))

        # update on result
        result = g.HOME_WIN
        if result == 1:                       # home won
            dwin = (rh + hca) - ra
        else:                                 # away won
            dwin = ra - (rh + hca)
        mult = mov_multiplier(g.MARGIN, dwin)
        delta = K * mult * (result - p_home)
        ratings[g.HOME_TEAM_ID] = rh + delta
        ratings[g.AWAY_TEAM_ID] = ra - delta

    return pd.DataFrame(rows, columns=[
        "GAME_ID", "GAME_DATE", "SEASON", "SEASON_TYPE",
        "ELO_HOME_PRE", "ELO_AWAY_PRE", "P_HOME", "HOME_WIN"])


def metrics(p: pd.Series, y: pd.Series) -> dict[str, float]:
    p = p.clip(1e-6, 1 - 1e-6)
    acc = ((p > 0.5).astype(int) == y).mean()
    logloss = -(y * p.apply(math.log) + (1 - y) * (1 - p).apply(math.log)).mean()
    brier = ((p - y) ** 2).mean()
    return {"n": len(y), "acc": acc, "logloss": logloss, "brier": brier}


def baseline_metrics(y: pd.Series) -> dict[str, float]:
    """Climatology baseline: predict the (in-sample) home win rate for every game."""
    base_p = y.mean()
    p = pd.Series(base_p, index=y.index)
    m = metrics(p, y)
    m["acc"] = max(base_p, 1 - base_p)  # always-home vs always-away, whichever better
    return m


def calibration_table(p: pd.Series, y: pd.Series, bins: int = 10) -> pd.DataFrame:
    cut = pd.cut(p, [i / bins for i in range(bins + 1)], include_lowest=True)
    tbl = pd.DataFrame({"p": p, "y": y, "bin": cut})
    g = tbl.groupby("bin", observed=True).agg(
        n=("y", "size"), pred=("p", "mean"), actual=("y", "mean"))
    return g


def main() -> None:
    games = pd.read_parquet(GAMES).sort_values(["GAME_DATE", "GAME_ID"])
    preds = run_elo(games)
    preds.to_parquet(FEAT_DIR / "elo_predictions.parquet", index=False)

    ev = preds[preds.SEASON != BURN_IN_SEASON].reset_index(drop=True)
    y, p = ev.HOME_WIN, ev.P_HOME

    print(f"Elo walk-forward backtest — evaluated on {len(ev):,} games "
          f"(burn-in {BURN_IN_SEASON} excluded)\n")

    elo = metrics(p, y)
    base = baseline_metrics(y)
    print(f"{'model':<18}{'acc':>8}{'logloss':>10}{'brier':>9}")
    print(f"{'Elo':<18}{elo['acc']:>8.3f}{elo['logloss']:>10.4f}{elo['brier']:>9.4f}")
    print(f"{'baseline (home)':<18}{base['acc']:>8.3f}{base['logloss']:>10.4f}{base['brier']:>9.4f}")
    print(f"{'coin flip (0.5)':<18}{'0.500':>8}{0.6931:>10.4f}{0.2500:>9.4f}")

    print("\nPer-season (walk-forward):")
    print(f"{'season':<10}{'n':>6}{'acc':>8}{'logloss':>10}{'brier':>9}")
    for season, grp in ev.groupby("SEASON"):
        m = metrics(grp.P_HOME, grp.HOME_WIN)
        print(f"{season:<10}{m['n']:>6}{m['acc']:>8.3f}{m['logloss']:>10.4f}{m['brier']:>9.4f}")

    rs = ev[ev.SEASON_TYPE == "Regular Season"]
    m = metrics(rs.P_HOME, rs.HOME_WIN)
    print(f"\nRegular season only: n={m['n']:,}  acc={m['acc']:.3f}  "
          f"logloss={m['logloss']:.4f}  brier={m['brier']:.4f}")

    print("\nCalibration (predicted home-win prob vs actual):")
    ct = calibration_table(p, y)
    print(f"{'bin':<14}{'n':>7}{'pred':>8}{'actual':>8}")
    for idx, r in ct.iterrows():
        print(f"{str(idx):<14}{int(r.n):>7}{r.pred:>8.3f}{r.actual:>8.3f}")

    print(f"\nSaved pre-game Elo features → {FEAT_DIR / 'elo_predictions.parquet'}")


if __name__ == "__main__":
    main()
