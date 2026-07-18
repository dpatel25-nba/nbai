# NBA Predictive Modeling & Backtesting Plan

*Derived from a cited, adversarially-verified research pass (22 claims confirmed via 3-vote
verification, 3 refuted and excluded). Goal: maximum predictive accuracy first, betting layer
later. Full method spectrum from strong baselines to state-of-the-art.*

---

## 0. The reality check that shapes everything

**Realistic pre-game NBA win-prediction accuracy is ~56–72%**, not the 90%+ figures you'll see
in some papers. Published pre-game benchmarks (systematic review, arXiv 2410.21484):

| Study | Method | Accuracy |
|---|---|---|
| Cao 2012 | Simple Logistic | 69.7% |
| Lin et al. 2014 | Random Forest | 65.2% |
| Horvat et al. 2020 | kNN | 60.8% |
| Zhao et al. 2023 | GCN + RF | 71.5% |

> ⚠️ **The 93.9% XGBoost accuracy (PLOS ONE 2024) is RETRODICTIVE** — it uses *full-game*
> box scores, which encode the final score. It is not forecasting and must never be cited as
> pre-game skill. The same model on partial-game features scored ~73–75%.

**Consequence:** since everyone is bunched near the same accuracy ceiling (the market baseline),
**calibration matters more than raw accuracy.** A model that says "65%" and is right 65% of the
time is worth more than one that's slightly more accurate but overconfident. So we evaluate with a
**calibration-aware battery** (Brier, log-loss, AUC, ECE/MCE), not accuracy alone.

---

## 1. Model sequencing (baselines → SOTA)

Build in this order. Each stage is a real, deployable model *and* a benchmark the next stage must beat.

### Stage 1 — Elo rating baseline (FiveThirtyEight-style)
The strongest simple, transparent baseline. Fully specified:

- **K-factor = 20**, long-term average rating **1500** (revert toward ~1505 between seasons;
  reversion is `new = 0.75*old + 0.25*1505`-style carryover — 538 uses 1/4 regression to mean each season).
- **Home-court advantage = 100 Elo points** (≈3.5 scoreboard points) in the simple model
  (~70 in the later RAPTOR-era model with explicit rest/travel terms; both are consistent).
- **Win probability:** `WP_home = 1 / (10^(-(elo_diff)/400) + 1)` where
  `elo_diff = elo_home - elo_away + 100 (home bonus)`. The **400** scale is standard Elo.
- **Margin-of-victory multiplier** (so blowouts move ratings more, with diminishing returns):
  `mult = ((MOV + 3)^0.8) / (7.5 + 0.006 * elo_diff_winner)`
  where `elo_diff_winner` is the winner's *home-adjusted* pre-game Elo edge (negative on upsets).
- **Rating update:** `elo_new = elo_old + K * mult * (result - WP)` with `result ∈ {1,0}`.
- **Projected margin:** `(elo_diff + 100) / 28` points.

> Source note: these are FiveThirtyEight's own published parameters. 538 has shut down (site
> redirects to ABC News), so the model is **frozen ~2023 and unmaintained** — but the methodology
> is correct and widely replicated (nicidob, ergosum). We treat it as a documented baseline, not gospel.

### Stage 2 — Efficiency / possession model (Four Factors + Pythagorean)
Dean Oliver's **Four Factors** (weights are approximate; modern regressions revisit exact splits):

| Factor | Weight | Formula |
|---|---|---|
| Shooting | ~40% | `eFG% = (FG + 0.5*3P) / FGA` |
| Turnovers | ~25% | `TOV% = TOV / (FGA + 0.44*FTA + TOV)` |
| Rebounding | ~20% | `ORB% = ORB / (ORB + opp DRB)` |
| Free throws | ~15% | `FT/FGA` (or `FTR = FTA/FGA`) |

- **Possessions** `≈ FGA + 0.44*FTA + TOV - ORB` — the `0.44*FTA` term estimates possessions
  ending at the line. Normalize everything **per 100 possessions** so pace doesn't distort ratings.
- **Pythagorean win%** (basketball exponents): `Win% = PS^14.3 / (PS^14.3 + PA^14.3)` regular
  season, **13.2** in playoffs. Feed projected points-for/against to get an expected win rate.

### Stage 3 — Gradient boosting (the ML workhorse)
- **XGBoost / LightGBM** are the documented best single models for NBA outcomes; XGBoost beat KNN,
  SVM, RF, LR, DT across five metrics (PLOS ONE 2024), LightGBM the closest.
- Feature inputs: Elo (Stage 1) + Four-Factors/efficiency features (Stage 2) + rest/schedule + form.
- **Stacked ensembles beat single models**: a 6-learner stack (XGBoost, KNN, AdaBoost, NB, LR, DT)
  with an MLP meta-learner hit ~83.3% (Nature Sci Rep 2025) vs ~74–81% for individual models — a
  ~2-point gain that was statistically significant. *(Note: that 83% is on their split/era; treat
  as relative evidence for stacking, not an absolute target for our data.)*
- Always fit a **probability calibrator** (Platt / isotonic) on a held-out slice.

### Stage 4 — Player-value ratings (RAPM / PIPM-style) as features
Decompose team strength from players so we handle injuries/lineup changes:

- **RAPM (Regularized Adjusted Plus-Minus):** build an `n_possessions × m_players` design matrix
  from play-by-play/lineup stints (+1 offense, −1 defense, 0 off-court), response = scoring margin
  per 100 possessions, solve with **ridge regression** (L2 penalty chosen by cross-validation).
  Ridge is what makes noisy raw APM stable.
- **PIPM** blends luck-adjusted plus-minus (strips variance a team can't control, e.g. opponent
  3P%), box score, and interaction terms, with separate O/D components. (Its "most accurate"
  claim is self-reported — use as inspiration, validate ourselves.)
- These become **team-strength features** fed back into Stage 3.

### Stage 5 — State-of-the-art extras (optional, measure the lift)
- **Bayesian hierarchical scoring model:** model FT / 2PT / 3PT counts with random effects —
  `log(θ_home) = att_home + def_away + c + home`, `log(θ_away) = att_away + def_home + c`,
  total points `TP = FT + 2*2PT + 3*3PT`. Gives principled uncertainty and team attack/defense
  decomposition. *(A refuted claim said negative-binomial hit 96.67% — do NOT cite that.)*
- **Tracking / shot-quality (xPTS):** expected points per shot from location/defender distance,
  aggregated to team shot-quality-for/against. **Open question:** how much *pre-game* lift this
  adds over Elo + Four Factors is unproven — treat as an experiment, normalized per-possession.

---

## 2. Feature engineering (strict point-in-time / no leakage)

The #1 cause of models that look great in backtest and fail live. **Every feature for a game uses
only information available before tip-off.** All rolling stats are **lagged** (current game excluded).

- **Rest & schedule:** days rest, back-to-backs (538 uses a **−46 Elo** penalty for 2nd night of a
  back-to-back), 3-in-4, 4-in-6, home-stand/road-trip length, travel distance, altitude.
- **Rolling form:** last-5 / last-10 / season-to-date **lagged** averages, per team and player.
- **Opponent adjustment / SOS:** adjust ratings for schedule strength; opponent DRtg faced.
- **Home-court:** ~70–100 Elo points (~3.5 pts) — but estimate it from our own data per season.
- **Normalization:** per-100-possessions everywhere so pace doesn't confound.
- **Leakage audit:** use SHAP to confirm no post-game feature is influencing predictions.

---

## 3. Backtesting protocol (non-negotiable)

- **Never use standard k-fold CV** — it leaks look-ahead information in time-series settings
  (confirmed against the Palomar portfolio-optimization text, scikit-learn TimeSeriesSplit docs,
  López de Prado). k-fold backtests here are "very dangerous... better avoided."
- **Use strict chronological / walk-forward splits.** Concrete protocol (MDPI Information 2026):
  - Train on seasons ≤ T, validate on T+1, test on T+2.
  - Roll the window forward across 2013-14 … present for cross-season robustness.
  - **Expanding window** (train on all history to date) mimics live retraining; **rolling window**
    (fixed lookback) tests recency. Report both.
- **Metrics battery:** Accuracy, **Brier score**, **log-loss**, AUC, and calibration
  (**ECE/MCE** + reliability diagram). Calibration is the primary bar.
- **Open question:** no verified concrete Brier/log-loss *target* survived (the ESPN Brier 0.075
  claim was refuted). We'll establish our own baseline by first measuring the Elo model, then
  beating it.

---

## 4. Betting layer (survey only — for LATER, and thinly sourced)

> The betting-specific claims did **not** survive verification well; this section is directional,
> to be researched properly when we actually build it.

- **De-vig** to get fair probabilities: convert American odds → implied prob
  (fav: `-O/(-O+100)`; dog: `100/(O+100)`), then normalize the two sides so they sum to 1.
- **Closing Line Value (CLV)** is the practitioner gold-standard for evaluating a betting model —
  did you beat the closing line? Backtest against **historical closing odds**, not results alone.
- **Market efficiency:** NBA lines are sharp; edges are small. One arXiv result notes you can
  profit with a model *less accurate* than the market if it's **decorrelated** from market prices —
  edge comes from disagreement, not raw accuracy.
- **Bankroll:** Kelly criterion `f* = (bp - q)/b`, but **full Kelly ruins** in practice (a 16k-game
  NBA experiment with Pinnacle closing odds hit 100% ruin at full Kelly when the model was in
  KL-disadvantage). Use **fractional Kelly** + drawdown constraints.

---

## 5. How this maps to our pipeline

- **Layer 2 (clean facts)** must expose: possessions, `0.44*FTA`, eFG%, TOV%, ORB%, FTR at team &
  player grain; play-by-play stints for RAPM; shot coords for xPTS. Our raw data already has all of this.
- **Layer 3 (features)** = Sections 2 (leakage-free features) + Stages 1–2 outputs (Elo, ratings)
  as columns.
- **Models** = Stages 3–5, each evaluated under the Section 3 protocol.
- **Betting** = Section 4, deferred.

## 6. Recommended build order
1. Elo baseline + walk-forward backtest harness + calibration metrics → **establishes our benchmark**.
2. Four Factors / efficiency features + Pythagorean.
3. Leakage-free feature layer (rest, form, SOS) with SHAP leakage audit.
4. XGBoost/LightGBM + calibration; then stacking.
5. RAPM/PIPM player features from play-by-play.
6. Bayesian + tracking/xPTS experiments (measure incremental lift).
7. (Later) betting layer with real closing-odds data.

## Open questions to resolve later
- Concrete calibration target (Brier/log-loss/ECE) for a good NBA pre-game model — establish empirically.
- Detailed betting spec (de-vig methods, CLV, documented market efficiency, Kelly sizing).
- Best RAPM ridge-penalty / prior / stint-construction recipe.
- Actual pre-game lift from tracking/xPTS over Elo + Four Factors.

## Key sources
- FiveThirtyEight — [NBA Elo](https://fivethirtyeight.com/features/how-we-calculate-nba-elo-ratings/),
  [predictions methodology](https://fivethirtyeight.com/methodology/how-our-nba-predictions-work/)
- Basketball-Reference — [Four Factors](https://www.basketball-reference.com/about/factors.html)
- PLOS ONE 2024 — [XGBoost + SHAP](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0307478)
- Nature Sci Reports 2025 — [stacked ensemble](https://www.nature.com/articles/s41598-025-13657-1)
- MDPI Information 2026 — [leakage-free backtesting](https://doi.org/10.3390/info17010056)
- arXiv 2410.21484 — [systematic review / benchmarks](https://arxiv.org/pdf/2410.21484)
- bball-index — [PIPM](https://www.bball-index.com/player-impact-plus-minus/);
  RAPM walkthrough — [jecutter](https://jecutter.github.io/blog/rapm-model/)
- Kelly/betting — [arXiv 2107.08827](https://arxiv.org/pdf/2107.08827)
