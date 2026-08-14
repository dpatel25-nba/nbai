"""
A conditional possession-outcome model, replacing stacked multiplicative patches.

The engine picks a possession outcome from a FIXED per-player mix — 2PA / 3PA /
free-throw trip / turnover — and then bends it with multipliers: transition
raises make probability and cuts the three-point share, endgame raises threes for
a trailing team, cold start lowers makes, defenders scale the make rate. Each was
measured honestly and each works. Stacking them does not.

That architecture has a concrete failure history in this project: adding a
mechanism silently broke the anchor THREE separate times (lineup-dependent
rebounding, transition, shot zones), because the closed form behind the
calibration did not know about the new multiplier. Every patch also multiplies
independently, so two effects that overlap in reality double-count.

The alternative is one model:

    P(outcome | player, context) = softmax(base_k + beta . context)

where base_k is the player's own log-rate for each outcome, carried as a feature
so the model starts from what he actually does, and context covers transition,
score state, time remaining and whether he just came on. Effects then compose
through the softmax by construction — probabilities sum to one, an increase in
one outcome necessarily comes out of the others, and there is a single place to
compute the expected value the anchor needs.

The question is empirical: does the conditional model predict actual possession
outcomes better than the base rates, and better than the base rates times the
patches the engine uses today? Held-out on a season it never saw.

Usage: python scripts/142_possession_model.py
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss

ROOT = Path(__file__).resolve().parents[1]
PBP = ROOT / "data" / "parquet" / "pbp"
PS = ROOT / "data" / "parquet" / "player_seasons.parquet"
GAMES = ROOT / "data" / "parquet" / "games.parquet"
OUT = ROOT / "data" / "parquet" / "possession_model.parquet"

TRAIN = ["2023-24", "2024-25"]
TEST = "2025-26"
LIVE = ("lost ball", "bad pass")
CLASSES = ["FG2", "FG3", "FT", "TOV"]


def extract(season: str) -> pd.DataFrame:
    """One row per possession that a player ended, with outcome and context."""
    p = pd.read_parquet(PBP / f"{season}.parquet",
                        columns=["GAME_ID", "PERIOD", "ACTION_NUMBER", "SEC_REMAINING",
                                 "TEAM_ID", "PLAYER_ID", "ACTION_TYPE", "SHOT_VALUE",
                                 "SCORE_HOME", "SCORE_AWAY", "DESCRIPTION"])
    p = p.sort_values(["GAME_ID", "PERIOD", "SEC_REMAINING", "ACTION_NUMBER"],
                      ascending=[True, True, False, True])
    p[["SCORE_HOME", "SCORE_AWAY"]] = (p.groupby("GAME_ID")[["SCORE_HOME", "SCORE_AWAY"]]
                                       .ffill().fillna(0))
    g = pd.read_parquet(GAMES, columns=["GAME_ID", "HOME_TEAM_ID"])
    home = {r.GAME_ID: r.HOME_TEAM_ID for r in g.itertuples()}

    rows, prev_kind, prev_team = [], None, None
    for r in p.itertuples():
        if r.ACTION_TYPE == "period":
            prev_kind, prev_team = None, None
            continue
        kind = None
        if r.ACTION_TYPE == "Made Shot" or r.ACTION_TYPE == "Missed Shot":
            if r.SHOT_VALUE in (2, 3):
                kind = "FG3" if r.SHOT_VALUE == 3 else "FG2"
        elif r.ACTION_TYPE == "Turnover":
            kind = "TOV"
        elif r.ACTION_TYPE == "Free Throw":
            kind = "FT"
        if kind is None or pd.isna(r.PLAYER_ID) or pd.isna(r.TEAM_ID):
            continue
        # free throws come in pairs; only the first of a trip counts as one possession
        if kind == "FT" and prev_kind == "FT" and prev_team == r.TEAM_ID:
            continue
        own = ((r.SCORE_HOME - r.SCORE_AWAY) if r.TEAM_ID == home.get(r.GAME_ID)
               else (r.SCORE_AWAY - r.SCORE_HOME))
        rows.append({"SEASON": season, "PLAYER_ID": int(r.PLAYER_ID), "y": kind,
                     "period": r.PERIOD, "rem": r.SEC_REMAINING, "margin": own,
                     "trans": int(prev_kind == "TOVLIVE" and prev_team != r.TEAM_ID)})
        if kind == "TOV":
            d = str(r.DESCRIPTION).lower()
            prev_kind = "TOVLIVE" if any(k in d for k in LIVE) else "TOV"
        else:
            prev_kind = kind
        prev_team = r.TEAM_ID
    return pd.DataFrame(rows)


def base_rates(df: pd.DataFrame) -> dict:
    """Each player's own outcome mix, shrunk toward league for thin samples."""
    lg = df.y.value_counts(normalize=True)
    out = {}
    K = 60.0
    for pid, d in df.groupby("PLAYER_ID"):
        n = len(d)
        c = d.y.value_counts()
        out[pid] = {k: (c.get(k, 0) + K * lg[k]) / (n + K) for k in CLASSES}
    return out, {k: lg[k] for k in CLASSES}


def featurize(df, base, lgp):
    """Context features plus the player's own log-rates, which act as the offset."""
    b = np.array([[base.get(p, lgp).get(k, lgp[k]) for k in CLASSES]
                  for p in df.PLAYER_ID])
    b = np.log(np.clip(b, 1e-4, None))
    late = (df.period >= 4) & (df.rem < 120)
    X = np.column_stack([
        b,                                       # 4 offset-like features
        df.trans.to_numpy(),
        (late & (df.margin < -2)).astype(float),   # trailing late
        (late & (df.margin > 2)).astype(float),    # leading late
        np.clip(df.margin.to_numpy(), -25, 25) / 25.0,
        (df.period >= 4).astype(float),
        np.clip(df.rem.to_numpy(), 0, 720) / 720.0,
    ])
    return X


def main() -> None:
    tr = pd.concat([extract(s) for s in TRAIN], ignore_index=True)
    te = extract(TEST)
    print(f"possessions — train {len(tr):,}  test {len(te):,}")
    print("outcome mix:", {k: f"{v*100:.1f}%" for k, v in
                           tr.y.value_counts(normalize=True).items()})

    base, lgp = base_rates(tr)
    Xtr, Xte = featurize(tr, base, lgp), featurize(te, base, lgp)
    ytr, yte = tr.y.to_numpy(), te.y.to_numpy()

    m = LogisticRegression(max_iter=2000, C=1.0, multi_class="multinomial")
    m.fit(Xtr, ytr)
    p_model = m.predict_proba(Xte)

    # baseline 1: the player's own mix, which is what the engine uses
    p_base = np.array([[base.get(p, lgp).get(k, lgp[k]) for k in m.classes_]
                       for p in te.PLAYER_ID])
    p_base = p_base / p_base.sum(axis=1, keepdims=True)

    # baseline 2: base rates times the engine's current multiplicative patches
    ci = {c: i for i, c in enumerate(m.classes_)}
    p_patch = p_base.copy()
    tmask = te.trans.to_numpy().astype(bool)
    p_patch[tmask, ci["FG3"]] *= 0.84                    # transition 3-share cut
    late = ((te.period >= 4) & (te.rem < 120)).to_numpy()
    trail = late & (te.margin.to_numpy() < -2)
    p_patch[trail, ci["FG3"]] *= 1.16                    # endgame three-chasing
    p_patch = p_patch / p_patch.sum(axis=1, keepdims=True)

    print(f"\nHeld-out {TEST} — predicting which outcome a possession produces")
    print(f"  {'model':<38}{'log-loss':>10}{'accuracy':>10}")
    for nm, pp in (("player base rates (engine today)", p_base),
                   ("base rates x current patches", p_patch),
                   ("conditional multinomial model", p_model)):
        acc = (m.classes_[pp.argmax(1)] == yte).mean()
        print(f"  {nm:<38}{log_loss(yte, pp, labels=list(m.classes_)):>10.5f}{acc:>10.4f}")

    print("\n  fitted context coefficients (log-odds, by outcome):")
    names = ["transition", "trailing late", "leading late", "margin",
             "Q4", "time left"]
    print(f"  {'':<16}" + "".join(f"{c:>10}" for c in m.classes_))
    for j, nm in enumerate(names):
        row = m.coef_[:, 4 + j]
        print(f"  {nm:<16}" + "".join(f"{v:>+10.3f}" for v in row))

    pd.DataFrame({"feature": ["base_" + c for c in CLASSES] + names,
                  **{c: m.coef_[i] for i, c in enumerate(m.classes_)}}).to_parquet(
        OUT, index=False)
    print(f"\nSaved -> {OUT}")


if __name__ == "__main__":
    main()
