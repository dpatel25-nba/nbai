---
name: predictive-findings
description: "Empirical results from rigorous leakage-free backtesting studies — what actually predicts games vs player props, and the data frontier"
metadata: 
  node_type: memory
  type: project
  originSessionId: 052674ad-d480-4e3c-940d-00c046841f89
---

Findings from deep walk-forward backtests (scripts 60-65), 2026-07-18. All leakage-free, permutation importance + model comparison. See [[north-star-vision]].

**Game outcome (near ceiling, ~65% acc, log-loss ~0.618):**
- Team strength (Elo) dominates ~80x over any other feature. It's a nearly LINEAR signal → simple LOGISTIC beats gradient boosting (GB overfits here).
- Most box stats (form, shooting%, net rtg) are REDUNDANT with a good rating. Only orthogonal add = rest/schedule (Elo can't know a team is tired).
- Confirmed 3 ways: game prediction is a solved, one-feature, ceiling-bound problem. NOT where our edge is.

**Player points (this is where the value is):**
- GRADIENT BOOSTING WINS here (nonlinear interactions) — opposite of games. Model choice depends on the problem; always test.
- Minutes DOMINATES (13x everything). Then recent scoring RATE (last-10), which BEATS the season projection. Opponent team-defense is minor (+0.017); rest/home ~0.
- Recent FORM is the biggest feature group add. Team-level opponent adjustment has a low measured ceiling → need player-SPECIFIC matchup (defender quality) for opponent effects to matter.

**Minutes (the dominant lever — deep-dived):**
- Our earlier player studies cheated with ACTUAL minutes (MAE 3.74). Real pre-game with PROJECTED minutes = MAE 4.62. Minutes uncertainty = 23% of all player-points error.
- Best minutes model = recent_min3 + started_last + proj_mpg. Plateaus at MAE ~4.83 / R2 0.62. Very recent games (last 3) dominate. Clever features (blowout risk, trend, volatility) barely helped.
- Starters far more predictable than bench (MAE 4.3 vs 5.6) → props confidence should scale with role.

**THE DATA FRONTIER (noted for a later update, user 2026-07-18):** the ~4.8-min minutes ceiling is a DATA problem, not a modeling one. ~38% of minutes variance is unpredictable from history but KNOWABLE from pre-game INJURY / INACTIVE / STARTING-LINEUP NEWS (available ~1hr pre-game, absent from our data). This is the single highest-ROI data source to add — deferred for now, revisit later. Foul trouble/in-game load = truly irreducible.

---
**Matchup-data investigation + the `vacated` win (scripts 85-91, 2026-07-18).** User goal: complex stats that ALSO perform in backtests. Parsed boxscorematchupsv3 → matchups.parquet (1.33M defender-offender rows, 2017-18…2022-23; pull was still completing later seasons).

- **Opponent signals are consistently too weak to move player props.** Rigorously falsified, in order: team opponent-defense (+0.017), opponent pace (~0), blowout (~0.01), and — the big test — PLAYER-SPECIFIC MATCHUP. Even the *idealized* "defense faced" (exact defenders who guarded him, prior-season skill, weighted by possessions) gave NO out-of-sample points gain; the real effect is only ~0.2 pts/game (elite vs weak D) — an order of magnitude below single-game noise. Offenses scheme AWAY from good defenders, so a stopper's value is denied touches, not lower efficiency when targeted.
- **Defender-quality v2 (matchup suppression, script 87):** opponent-adjusted + assignment-adjusted (raw suppression correlates −0.59 with minutes — bench guys hidden on weak scorers; MUST residualize on minutes+assignment difficulty). Face-valid leaderboard (Draymond, Marcus Smart, Caruso, Horford, DFS). BUT face validity ≠ predictive validity: it adds ZERO portable signal to team defensive rating over box DBPM (incremental R² +0.000, script 89). On-ball matchup D is a small slice of team D (scheme/help/rim dominate). Keep as DESCRIPTIVE website content only, not a predictive input.
- **THE VALIDATED WIN — the `vacated` family (own-team availability).** When a rotation teammate is OUT, this player absorbs minutes+usage. First complex features to genuinely beat the backtest. Three, all in PRODUCTION (scripts 70+71):
  - `vacated_min` = absent rotation teammates' projected MINUTES (beats scoring-based version, script 91); `vacated_pos` = absent minutes at focal player's own position (only helps combined w/ vacated_min).
  - `vacated_delta` = recent-avg(vacated_min over player's last 10 games) − tonight's vacated_min (script 92). THE BIG ONE: 3rd-most-important minutes feature (+0.40), behind only recent_min3/10. It corrects the STALENESS in recent_min — recent minutes overstate tonight's role if recent games were propped up by absences now resolved (or vice versa). This is the key insight: the dominant feature (recent_min) is stale under roster change; vacated_delta fixes it.
  - vacated also feeds the RATE models (not just minutes): teammates out -> usage spike -> higher per-min scoring (points 4.598->4.568). Two distinct channels, both in production.
  - **Round 3 — the player's OWN availability (script 93, also in production):** `load3` = player's minutes in last 3 calendar days (fatigue/load-management -> rest risk; 5th-most-important minutes feature +0.27); `own_missed3`/`own_missed10` = # of team's last 3/10 games the player MISSED (return-from-injury ramp; recent_min can't see this since it only averages games he played). Cheap once team schedule (incl. missed games) is loaded.
  - **Round 4 — hot-hand decomposition (script 94, in production, POINTS only):** split recent scoring into VOLUME (`recent_fga36_5/10` = FGA/36, sticky) vs EFFICIENCY (`recent_ts5/10` = TS%, regresses) + `recent_ts_delta` (recent TS% − season-to-date TS%). Lets the model regress a hot-shooting stretch instead of projecting it. Efficiency does most of the work. Points 4.560→4.536.
  - Cumulative effect: engine **minutes MAE 4.87→4.60, points 4.64→4.536, reb 1.95→1.912, ast 1.36→1.310**. Broke the ~4.83 minutes plateau decisively.
  - Discarded as dead (tested, no signal): min_trend, blowout, opp_pace, vacated_max, vacated_pos-alone, blow_bench (blowout×bench interaction).
- **Takeaway:** the edge is NOT opponent-side — it's the player's OWN TEAM CONTEXT (who else is available, and how that differs from his recent norm). That's where non-shallow complexity earns its keep. Next in this vein: usage-compression on star return, role-stability signals. Injury/inactive/lineup NEWS (see frontier above) remains the highest-ROI unpulled data source — user agreed to add it "at some point" (2026-07-18).

---
**Advanced-tracking pull + its verdict (scripts 95-99, 2026-07-19).** Pulled overnight (all raw JSON): leaguedashptstats (tracking splits, 2013-14+), synergyplaytypes (play-type efficiency off+def, 2015-16+), leaguedashptdefend (opp FG% by zone), per-game hustlestatsboxscore (2015-16+, ~14.1k games — contested shots, deflections, screen assists, box-outs, charges). Parsers: 96 (player_tracking.parquet, 147 cols), 99 (player_synergy + team_synergy).

- **PROPS MEANS ARE SATURATED — confirmed decisively.** Projected tracking shot-diet (drives, catch&shoot vs pull-up, touches, pts/touch) does NOT improve points (4.536→4.539), and tracking OPPORTUNITY stats (reb chances, potential assists) do NOT improve reb (1.912) or ast (1.310). Redundant with the projected rates already in the model. Every data type now tested against props means — box, opponent, matchup, full tracking layer — and only own-team context (vacated/load3) + hot-hand decomposition ever moved them. Stop hunting props-mean features; the ceiling is real.
- **SYNERGY IS THE SIMULATOR FOUNDATION (validated).** Freq-weighted team synergy PPP vs actual team ratings: offense r=+0.943, defense r=+0.972 (330 team-seasons). Decomposes team quality BY PLAY TYPE (transition/iso/PnR-handler/PnR-roll/postup/spotup/handoff/cut/offscreen/offreb/misc), each with frequency (poss_pct) + efficiency (ppp), offense AND defense. The matchup grid (team A offensive mix × play-type PPP vs team B defensive PPP-allowed) is the bottom-up simulator's core computation — this is the RIGHT use of the new data, the north-star [[north-star-vision]]. team_synergy.parquet / player_synergy.parquet ready.
- Still unparsed: leaguedashptdefend + hustle (for a proper defensive profile — the descriptive defensive gap; box stats can't measure defense).

---
**FIRST NEW-DATA PREDICTIVE WIN — synergy offensive value (script 109, 2026-07-19).** The WAR bake-off (user's original ask) is paying off where betting didn't. Built synergy-based offensive value = Σ_playtype poss·(player_PPP − league_PPP) — points created above average via play-type efficiency. Validated: corr with box OBPM +0.54 (agree but non-redundant), team-additivity +0.60, YoY stability +0.63 (as stable as OBPM), face-valid (SGA/Jokić/Durant/Lillard/Brunson top). THE WIN — leave-one-season-out CV predicting team ORtg from PRIOR-season player value: box OBPM only RMSE 2.605, synergy only 2.542, **box+synergy 2.366 (−9% error, corr 0.579→0.672)**. Cross-validated, honest. Why it works when betting-edge/matchup stuff didn't: this is PLAYER VALUATION (not market-beating) — synergy sees play-type skill box stats can't decompose. The new data's real value is BETTER PLAYER METRICS, not betting edges. EXTENDED (scripts 110-113): defensive analog — tracking RIM PROTECTION (DEF_RIM_FGA·(lg−player rim FG%)) beats box DBPM for team-defense prediction (LOSO RMSE 2.675→2.607; matchup skill still adds nothing, confirms 89); face-valid (Holmgren/Wemby/Gafford/Gobert). PLAYMAKING/creation (AST_POINTS_CREATED + POTENTIAL_AST) adds on top of synergy (box+syn 2.503 → +creation 2.352, −12.8% vs box alone). **Composite WAR: v1 box team-win corr 0.837 → v2 (+synergy+rim) 0.858 → v3 (+creation) 0.868** — each new-data source adds, monotonic, Jokić correctly #1 once creation captured. player_seasons_war_v3.parquet built (OBPM3/DBPM3/BPM3/WAR3). READY TO SHIP to website Players tab (replaces box-only WAR). This is the successful offline-research thread — new data's payoff is player valuation.

REFEREE test (scripts 107/108, 2026-07-19) — NULL, like fatigue. Pulled 2,460 games' ref assignments (boxscoresummaryv2 Officials, 2021-22+2022-23). Crew leave-one-out totals tendency: corr with actual total +0.044, with total−opening +0.051 (tiny, mostly priced). Adding ref crew to sim's total does NOT improve the opening-total edge (58.4% vs 58.9%). Refs have a real but tiny, market-priced effect. Confirms: the market prices everything publicly knowable.

---
**AUDIT CORRECTIONS (3-agent + self audit, 2026-07-19) — READ BEFORE TRUSTING HEADLINES.** Deep leakage/data/feature audit. Pipeline is CLEAN: exact cross-table reconciliation (team_games==games==Σplayer_games, 0 mismatch/33,516), no nulls in join keys, walk-forward + Marcel projections + recent-form deque ordering all verified leakage-free, SBR game-lines parse correct (0.29% favorite disagreement). BUT two optimistic headlines corrected:
- **`vacated` minutes gain is ~10-20% hindsight.** `participants` is built from MIN>0 (final box score), so "who's out tonight" uses end-of-game truth, not a pre-game injury report. Measured: 79.6% of absent-minutes are "continuing" (player also out prior game = pre-game knowable, legit); 20.4% "new/isolated" (surprise scratch/DNP-CD/rest = hindsight). Honest deployed minutes MAE ≈ **4.62-4.65, not 4.60**. Points impact minor (~4.63→4.59). Real signal, over-credited. Proper fix needs a historical pre-game injury feed (don't have).
- **Totals edge is THIN, not the 57% reported.** That used a top-20% threshold chosen on the FULL test set (data snooping). Honest WALK-FORWARD threshold (cutoff from prior seasons only): **53.78% over 1,813 bets (55.68% recent 2019-20+), z=1.19 vs breakeven — NOT statistically significant.** Survives OOS (positive, consistent, stronger recently) but unproven; needs more data/forward validation before it's a tradeable edge. Same in-sample-threshold caveat applies to 105/106.
- Minor (documented, low impact): sim_mode1 MARGIN_SD/TOTAL_SD fit in-sample (affects P_HOME calibration slightly); median imputation uses test-set stats (97/98/101); rotation membership uses full-season GP; neutral-site pbp home/away orientation swapped (~8 games). FIXED: defender_quality_v2 "confound removed" was tautological (OLS guarantees corr≈0) → reframed as descriptive-only; game_lines ~4 garbage totals → sanity-guarded.

---
**CLV CONFIRMS THE TOTALS EDGE IS REAL (2026-07-19) — strongest betting result.** Closing Line Value = do we get a better number than the close (the sharp-bettor gold standard, far less noisy than win rate). Betting the OPENING total in sim_mode1's direction: mean CLV **+0.68 pts, 55.4% positive**, and MONOTONIC in disagreement size (all +0.68 → top40% +1.18 → top20% +1.52 → top10% +2.03). Spreads: mean CLV +0.14, 46% positive → NO edge (consistent w/ ATS null). CLV>0 bets beat the close 50.5% vs CLV<0 47.6% (validates CLV is real). Interpretation: the market systematically moves toward our total after open; we reliably beat the closing total. This is MORE credible than the noisy win-rate test (53.8% OOS "not significant") because CLV is the leading indicator of long-run profit and it's clearly positive. Real, durable, but MODEST (+0.68 avg, +2.0 on strongest) and realizable only at opening (low limits, bet early). The one edge that survives every honest test. Driver: sim_mode1 pred_total (walk-forward). WAR v3 SHIPPED to website (Players tab, commit 6e3d846).

---
**FREE EDGE FOUND — we beat the OPENING TOTAL line (scripts 102 re-parse, 2026-07-19).** The closing line is unbeatable, but OPENING lines are soft. Re-parsed the SBR archive for the Open column too (free, cached HTML). Findings: (1) corr(our disagreement with opening spread, line move open→close) = +0.60 — the market moves toward our number, so our model holds real info the opening line hasn't priced. (2) On TOTALS vs opening, selective betting of our biggest disagreements is PROFITABLE: all 51.8%, top-20% 53.9%, top-10% 55.5%, top-5% 58.7% (monotonic = real). Robust by season and STRONGER recently: 2020-21 58.6%, 2021-22 60.4%, 2022-23 55.3%; 2019-20+ pooled top-20% = 57.1% over 802 bets (~4 SE above coin-flip). ATS/spreads: NO edge (spreads sharper than totals). Driver = sim_mode1 pred_total (walk-forward, leakage-free). Caveats: opening totals have LOW limits; must bet at open (operational); exact top-20% cutoff is somewhat data-chosen (existence robust, magnitude confirm forward). IMPLICATION: soft markets ARE beatable — validates that player-prop markets (also soft) are worth pursuing. Next: improve the TOTALS model (the edge engine) + build a live opening-total edge flagger. This is the first genuine market-beating result, and it's FREE.

---
**WE DON'T BEAT THE GAME MARKET — and it pinpoints the lever (scripts 102-103, 2026-07-19).** Pulled free historical GAME lines (spread/total/moneyline) from sportsbookreviewsonline, 2013-14…2022-23, 12,124 games matched to our GAME_IDs → game_lines.parquet (player props NOT freely available — the-odds-api is paid/metered). Market-edge backtest vs closing lines (10,182 games): ATS 49.2%, totals 49.9%, ML ROI −2.85% (sim)/−1.71% (Elo) — all below breakeven. Our Brier 0.217 vs market **0.207** (market sharper). Our highest-confidence disagreements are WORST (top-5% conf → 46.9% ATS): where we most disagree, the line is right. **Key reframe:** the market's ~0.011 Brier edge = the value of the info it has and we don't (injury/lineup news + sharp money) — the SAME wall capping our props minutes. Injury/inactive/lineup NEWS is now triply-confirmed as THE predictive-gain lever. **The opening:** game markets are efficient but PLAYER-PROP markets are soft (low limits, slow to move) — our validated own-context edge (`vacated`: teammate out → usage spike) targets exactly what props are slow to price. Plausible we beat prop markets even though we can't beat game markets — but testing needs paid prop-line data. game_lines pull = scripts 102 (pull/parse) + 103 (edge test).

---
**BOTTOM-UP MATCHUP DOESN'T BEAT TOP-DOWN (script 100, 2026-07-19) — key north-star finding.** Tested the simulator's core thesis: predict each team's actual per-game offensive rating, same offense baseline, opponent adjustment via play-type-weighted defense (bottom-up) vs aggregate def rating (top-down). Prior-season synergy profiles, 23,958 team-games. Result: top-down MAE 9.123 / corr 0.279 BEATS bottom-up 9.152 / 0.270. Regressing actual residual on both adj terms: top-down weight +0.772, bottom-up **−0.304** (adds nothing beyond aggregate, slightly counterproductive). Why: good defenses defend most play types well (play-type defense highly correlated → weighting ≈ aggregate), and prior-season play-type mix is stale. Same lesson as everywhere: aggregates dominate, opponent-specific granularity doesn't earn its complexity for PREDICTION. **Implication:** the play-type/synergy data is descriptively excellent (r≈0.95) and good for website narrative + a GENERATIVE simulator (realistic box-score distributions, prop variance) — but NOT a more accurate point predictor. The bottom-up-beats-top-down premise behind the matchup simulator does not hold for predictive accuracy. Caveat: only had season-aggregate synergy; a within-season/game-level play-type feed (not available) might fare better, but the aggregate-dominates pattern is very consistent.

---
**THE POSSESSION SIMULATOR — BUILT, AND IT NOW BEATS ELO (scripts 120-140, 2026-08-13).** The capstone from [[north-star-vision]] exists. Stint reconstruction (120) rebuilds the on-court five for both teams at every moment — 468k stints, 15,669 games, validated at 0.058 min MAE against box minutes. Three feed traps had to be solved: `actionNumber` is ENTRY order not game order (the NBA files corrections late); period-start lineups are absent from the feed entirely (6 subs at the top of Q3 vs 15,518 within it), so each period's five is inferred independently; and pbp names are unaccented while the box score is not. On top: rotation engine (121, 33% better than flat minutes), assignment model (122), possession engine (124), and a CLOCK-DRIVEN play-by-play simulator (134) that emits a real timestamped event log with substitutions and quarter scores.

**HELD-OUT 2024-25, 245 games, none of it tuned on that season:** sim acc 0.645 / logloss 0.6188 / brier 0.2156, vs Elo 0.620 / 0.6404 / 0.2266 and Mode-1 0.645 / 0.6152 / 0.2157. **Beats Elo on all three, ties Mode-1 on accuracy and Brier**, marginally behind on log-loss. Margin MAE 11.19 vs Mode-1 11.29; total 14.37 vs 14.17; bias +0.7. Player points 4.750 vs the props engine's 4.566 — still behind the purpose-built model. Earlier in the same session, before the mechanisms below, the identical held-out test put the sim behind BOTH baselines.

**MECHANISMS THAT EARNED THEIR PLACE.** Transition: the first shot after a LIVE-ball turnover is worth 1.213 pts vs 1.059 after a make (+13.8%), and defensive rebounds do NOT create transition (1.026 — worse than conceding a basket). Shot zones: rim 1.357 pts/shot vs mid-range 0.829, a 64% spread previously collapsed into one FG2_PCT. Height matching: defenders guard their own size (within 2 inches 1.340x availability, 4+ either way ~0.65x), −5.6% share MAE; weight adds −1.7% more. Pace RAPM on possession DURATION: −4.2%. In-season rate updating: −4.1% player points, and the in-season K (~200) is 5x smaller than the across-season K (~1000) because there is no aging or role drift to discount within a year. Per-stat empirical-Bayes shrinkage (126): 9/12 stats improved; volume stats were being over-shrunk (3PA K=50) while shooting percentages were under-shrunk (3P% K=3200).

**NULLS, ALL PROPERLY CLOSED.** Lineup chemistry: split-half correlation of residuals −0.039, and only 199 of 30,829 lineups ever have enough sample to score — unmeasurable, not merely unproven. Within-game fatigue: no decline; the real pattern is a COLD START (worse in the first 3 minutes). Assist networks: dead across seasons AND within-season, with accuracy rising as pair information is erased. Team-level tempo: −0.07%. Rebound chances: conversion is a real skill (YoY +0.755) but chances x conversion = rebounds by construction, so unusable for allocation. RAPM: face-valid (Jokić, Giannis, Draymond, Herbert Jones) but BPM3 beats it at predicting next-season team net rating (+0.729 vs +0.627), which validates the simulator's existing roster-sensitivity basis.

**THE METHODOLOGICAL LESSON — see [[null-results-check-identifiability]].** Before accepting a null, verify the quantity being regressed CAN vary as the model assumes. Pace RAPM first came out as nonsense (Zubac the league's fastest player, −0.01%) because possession COUNT is shared between the two teams by construction — the regression asked how each player changes a number identical for both. Respecified against possession DURATION it is worth −4.2%. Related: the theoretical estimator lost three times (split-half reliability, Kalman gain, raw pair counts), each beaten by a direct search on held-out error. And when comparing two models, check they share a baseline — mismatched intercepts made team tempo look worth 3.7% when centring collapses it to 0.07%.

**GRANULARITY WON THREE TIMES, AFTER LOSING SIX.** Lineup tempo, height, weight. All three replace a coarse proxy (team, position) with a DIRECT MEASUREMENT of a player property. The six losses — opponent defence, matchup suppression, play-type matchup, referees, assist pairs, opponent tempo — all tried to condition on a specific OPPONENT or PAIR. Useful rule: measuring a player better tends to work; conditioning on who he faces tends not to.

**STILL BLOCKED ON DATA.** Aging curves (139) and rookie priors are both blocked by the same survivorship filter — the bio pull initially covered only players active since 2022-23, so 100% of the sample survived and every aging peak came out ~4 years late (scoring 30, assists 36, TS% 38 against a 26-30 consensus). The estimator is sound and committed unused; the fix is the full-archive bio pull, in progress. Injury/inactive news remains THE lever, now quadruply confirmed — the simulator takes actives as an INPUT precisely because that information does not exist in our data.

---

## Aging, rookie priors, and two unmodelled rules (August 2026)

**THE SURVIVORSHIP BLOCK IS CLEARED.** The full-archive bio pull (1,706 players) unblocked both aging and rookie priors. Aging was then rebuilt as a DELTA method with an explicit mean-reversion control, and validated WALK-FORWARD — curves fitted on seasons before 2021-22, scored on 2021-22 onward. The finding that matters is not the peak ages but WHICH metrics age at all:

| adopted (age adjustment beats age-blind) | rejected (it makes projections worse) |
|---|---|
| MPG −3.31%, PTS_36 −2.49% | DREB_36 +2.64%, FG3_PCT +1.12% |
| FTA_36 −1.43%, TS_PCT −1.30% | STL_36 +1.06%, REB_36 +0.96% |
| FGA_36 −1.19%, TOV_36 −0.53% | OREB_36 +0.95%, FG3A_36 +0.72% |
| FG_PCT −0.46%, PF_36 −0.31%, FT_PCT −0.27%, AST_36 −0.14% | BLK_36 +0.64% |

**USAGE AND ROLE AGE; REBOUNDING, STEALS, BLOCKS AND THREE-POINT ACCURACY DO NOT.** Only the validated columns are wired into `build_rates` (`AGE_APPLY` in 124), applied as curve(age) − curve(age−1) because a Marcel projection describes the recent past and the target season is a year later. Verified directionally by A/B: ages 20-23 gain +0.66 MPG, 32+ lose −1.42, monotonic across buckets. Three earlier wrong turns are recorded in the script — residual-vs-Marcel peaked at 20 because Marcel's shrinkage correlates with age; the minutes threshold was blamed and was irrelevant from 400 down to 100; and the peaks were judged against a 26-27 consensus that refers to VALUE, not per-36 rates.

**DRAFT SLOT PREDICTS ROLE, NOT SKILL.** Rookie priors (141): MPG R²=0.285, FGA_36 0.220, PTS_36 0.197 — but every shooting percentage is ≈0. A high pick is given the ball; he does not shoot better with it. Wired into `build_rates` via `rookie_profiles()`, replacing a flat replacement profile that made the first overall pick and an undrafted two-way simulate identically, on 10.7% of 2025-26 minutes.

**TWO RULES THE ENGINE DOES NOT KNOW EXIST.**

*The bonus* (144). Once a team commits its fifth foul in a period, non-shooting defensive fouls become two free throws. The engine has no team-foul counter. The pbp turns out to record the state directly — descriptions carry `(P2.T3)`, running T1-T4 and then the literal **`PN`** once in the penalty; a regex for `T\d+` alone silently finds ZERO penalty fouls, which is how the first version of this script reported 1.59 non-shooting fouls per game against a real 38.4 total. Read correctly, the rule falls out of the data unprompted: Personal/Loose Ball fouls produce free throws **1% of the time under the limit and 100% in the penalty**. Worth 5.99 FT/game, 13.7% of all free throws, ~4.7 points. THE BUILD IS NOT "ADD THE BONUS" — the engine's foul rates were fit to reproduce observed FT totals, so they already carry these trips implicitly. Adding a counter on top would double-count, the same error that broke the anchor three times. The correct build splits the existing rate into shooting and non-shooting and gates the non-shooting half behind the counter: same total, correctly concentrated late in periods, and finally able to represent intentional fouling.

*The shot clock* (145). No shot clock exists in the data, so possession elapsed time is reconstructed from event timestamps by an explicit state machine. Identifiable — within-player sd of elapsed time is 7.0s. Within-player conversion, monotonic from 8s on:

```
0-4s  +0.028    8-12s  -0.001    16-20s  -0.044
4-8s  +0.055   12-16s  -0.013    20-24s  -0.101
```

A **0.156 pts/shot within-player spread, 14.3% of the mean**, with 18% of all shots in the late (≥18s) bucket at 1.015 pts/shot against 1.131 early. Second-chance possessions are scored separately because an offensive board resets the clock to 14 while elapsed time keeps accumulating — pooling them mixes two effects that point in opposite directions. The engine's transition flag fires only after a live-ball turnover and is a small slice of the early bucket, so most of this gap is currently unmodelled. When it is built, the multiplier must be MEAN-ZERO over the duration distribution the engine generates, or the anchor breaks a fourth time.

**THE PENALTY IS BUILT (124).** Implemented as a SPLIT, per the double-count reasoning above: `PEN_FT_TRIM = 0.137` is removed from the base free-throw branch in `_profile`, and re-routed through a per-period team-foul counter that turns any non-shooting foul past the 5th into two shots. Conservation verified on a paired run — free throws 44.50 → 44.70/game, points 224.8 → 224.9, penalty free throws 5.67 against a real 5.99, and the anchor TIGHTENED (−0.55/+0.14 → −0.32/−0.03).

**THE ANCHOR BROKE A FOURTH TIME GETTING THERE, IN A NEW WAY.** Two distinct errors, both worth recording because neither was visible in totals:

1. *Real-game constants describing an engine that behaves differently.* The closed form used the measured real-game figures — 21.4% of non-shooting fouls occur in the penalty, at `NONSHOOT_FOUL` = 0.092/possession. The engine actually reaches the penalty on **14.0%** of them, and its non-shooting draw fires INSIDE the putback loop so its effective rate is **0.102**, not 0.092. The anchor must expect what the SIMULATOR does, not what the NBA does. Replaced all three assumed constants with one directly measured from the engine (`PEN_TRIPS = 0.0142` trips/possession).

2. *Adding an increment as a total.* The closed form added the penalty's full points on top of the ordinary mix, but a penalty trip REPLACES a possession rather than adding one. The correct term is `PEN_TRIPS * (2*FT% − ppp)`, not `PEN_TRIPS * 2*FT%`. Adding the total pulled the engine 1.1 points per team below its anchor. Generalised rule, beyond this mechanism: **when a new branch consumes an existing possession, the anchor takes the difference between the branch's value and the mix it displaced — never the branch's value alone.**

A methodological note on how the second bug was nearly missed: the first "penalty OFF" control left the branch enabled and only zeroed the trim, so it measured the trim's effect and attributed it to the branch. That produced a confident, wrong conclusion (that the branch over-fired by 45%) which survived one round of reasoning. Only a TRUE baseline — branch disabled, trim zero, AND the anchor's penalty term zeroed — exposed it. An ablation control that leaves the mechanism running is not a control.

## The over-dispersion is possession INDEPENDENCE, not noise (August 2026)

**THE ABLATION KILLED MY OWN PLAN.** The standing theory was that the simulator's margin spread came from stacked injected randomness, to be fixed with an Ornstein-Uhlenbeck latent-efficiency process. Script 143 switches every stochastic source off one at a time, paired on the same games and seeds:

```
configuration            margin sd  total sd  team sd   corr   var share
  full model                 16.81     20.72    13.34   0.204
  - shared pace draw         17.31     18.92    12.82   0.089     -6.0%
  - shared shooting          17.08     18.98    12.77   0.103     -3.2%
  - team shooting            16.64     20.70    13.28   0.214     +2.0%
  - usage variation          16.96     20.71    13.38   0.199     -1.8%
  - minutes jitter           16.81     20.72    13.34   0.204      0.0%
  ALL injected noise off     16.70     17.24    12.00   0.033
```

**Every injected noise source together is worth 0.11 points of margin sd.** Removing the shared sources INCREASES margin spread (16.81 → 17.31) while cutting total spread and correlation — which is exactly right, since shared pace is what couples the two teams, and it confirms those components work. They are simply not where the variance is. An OU process would have added variance the model does not need: **the fix pointed in the wrong direction, and only the ablation revealed it.**

**WHERE IT ACTUALLY COMES FROM.** Script 146 measures points per possession on both sides with one definition — a possession runs until the offensive team changes, so and-ones, shooting-foul trips and putback chains stay inside the possession that produced them:

| | real 2024-25 |
|---|---|
| possessions | 258,041 (98.2 per team-game) |
| mean points | 1.1560 |
| **variance** | **1.4417** |
| outcomes | 0 pts 48.8%, 1 pt 3.1%, 2 pts 32.3%, 3 pts 15.6% |

If possessions were INDEPENDENT this variance implies a margin sd of **16.83**. The engine produces **16.81**. The engine's per-possession variance is therefore correct — it is behaving exactly like independent possessions, because that is what it is.

**REAL BASKETBALL IS SUB-BINOMIAL BY 5.6%** (15.88 actual against 16.83 independent). Possessions are negatively correlated in real games — garbage time, score-dependent effort, coaching adjustments — and the engine has garbage time but nowhere near enough compression. Against a conditional target of ~14.7 (`sqrt(15.88² − 6²)`, a forecast that knows the rosters must be narrower than the league-wide spread), the engine needs ~24% less margin variance, and NONE of it can come from turning knobs down. The next step is to measure score-dependent efficiency feedback directly in the play-by-play and build that, which is a mean-reverting force rather than an added noise term.

**TWO DATA TRAPS FOUND WHILE MEASURING THIS**, both of which produced confident wrong numbers that survived a first reading. `SHOT_RESULT` is **null on all 57,309 free-throw events** — makes are recorded only as a `MISS` prefix in the description text — so the first run silently dropped every free throw and reported a mean of 0.983 instead of 1.156, exactly the ~17 points per team that free throws are worth. And closing a possession eagerly on a made shot pushes and-one free throws into the OPPONENT's next possession. Both now carry a sanity guard that refuses to report a variance when the mean PPP is impossibly low. Recurring lesson, now four for four: **a derived quantity that lands near a plausible value is not validated; check it against a known total.**

## Score-dependent feedback is a mirage; the noise is shared, not margin-widening (August 2026)

**THE OBVIOUS MECHANISM IS DEAD, AND A PERMUTATION TEST IS WHAT KILLED IT.** Script 147 measures whether a leading team scores less per possession, using a team-game fixed effect so that team quality cannot masquerade as feedback. The raw estimate looked excellent: a monotonic gradient from +0.144 pts/possession at −20 to −0.128 at +20, slope −0.00493, exactly the compressing force the engine needs.

It is entirely an artifact. **Demeaning by a team's own game mean forces its deviations to sum to zero, so a team that scored more early MUST score less later — and it is ahead precisely because it scored more early.** Permuting each team's possession outcomes within the game preserves every value, the mean, the possession count and the artifact, while destroying any real state dependence:

```
observed slope     -0.00493 pts/possession per point of lead
permuted null      -0.00520  sd 0.00017  (6 shuffles)
excess              +0.00027   <- the entire effect is artifact
```

The test had power to spare: explaining the observed compression needs a slope near −0.00056, which is 3σ from this null, and the measured excess has the WRONG SIGN. There is no score-dependent efficiency feedback to build.

**A LEAKAGE BUG NEARLY SOLD ME THE OPPOSITE CONCLUSION FIRST.** The initial run put the slope at +0.0055 — leaders scoring MORE — with a raw gradient from 1.03 to 1.33 ppp. That 30% swing is what gave it away: the margin was read from the ffilled score AT the possession's first event, which on a scoring play already contains that possession's own points, so a possession that scored 3 mechanically showed a margin 3 higher. Lagging the score by one event flattened the raw gradient to 1.14–1.17, which is the sanity check. **A monotonic, plausible, well-signed gradient is not evidence of anything.**

**WITHIN-GAME MEAN REVERSION IS REAL BUT FAR TOO SMALL.** Splitting each team-game alternately (removes time ordering) versus chronologically (keeps it): r = +0.0008 alternating, −0.0273 chronological, a gap of −0.028. Teams do cool off after starting hot, but that buys ~1.4% of sd compression against the 5.6% to explain.

**WHAT THE ABLATION ACTUALLY SAYS, RE-READ.** The injected noise is **~93% shared between the two teams**, so it raises team variance and covariance together and margin variance barely moves — which is why the margin sd looked insensitive:

| | engine | real |
|---|---|---|
| team score sd | 13.34 | 12.69 |
| total sd | 20.72 | 19.79 |
| corr(home, away) | 0.204 | 0.217 |
| margin sd | 16.81 | 15.88 |

The engine's floor team sd is 12.00, BELOW real; injected noise takes it to 13.34, overshooting 12.69. So there IS a real calibration win available — cutting `SHOOT_SD_SHARED` and `PACE_SD` fixes the total and team spread — but it will not fix the margin, which sits on the possession-independence floor of 16.70. Those are two different problems and the ablation was needed to see that they are not the same one.

### CORRECTION: significance actually tested (August 2026)

Two claims in the section above were asserted without a test. Tested properly, both were wrong — in opposite directions.

**The feedback slope is NOT "entirely artifact".** The first permutation null used 6 shuffles, which is far too few to locate the null mean; at 40 shuffles the null mean moves from −0.00520 to −0.00537 and the picture changes:

```
observed         -0.00493
permuted null    -0.00537   se 0.00013  (40 shuffles)
excess           +0.00044   z = +3.45      <- significant
z against the -0.00056 needed = +7.82
```

There IS a significant effect, at 3.45σ, but it has the **wrong sign**: leading teams score slightly MORE, which widens margins rather than compressing them. The practical conclusion is unchanged and in fact strengthened — the compressing force is ruled out at 7.8σ, more firmly than a null would rule it — but "no measurable feedback" was the wrong description of what the data says. The script's verdict text now reports sign and effect size instead of a percentage, because "9% of the slope is real feedback" is true and completely misleading.

**Within-game mean reversion is NOT significant.** It was reported as "real but tiny". Bootstrapped over 2,628 team-games:

```
gap (chronological - alternating)  -0.0281
bootstrap se                        0.0193    z = -1.46
95% CI                             [-0.0659, +0.0081]   p(gap>=0) = 0.071
```

It does not clear 5%. The honest statement is that within-game mean reversion is **not detectable at this sample size**, not that it is small — the CI comfortably admits both zero and an effect three times larger than the point estimate.

**A STANDING WEAKNESS THIS EXPOSES.** Held-out percentage improvements have been reported throughout this work without error bars, and the sign of a held-out delta has been treated as a pass/fail. That is fine for MPG (−3.31%) or PTS_36 (−2.49%), and NOT fine for AST_36 (−0.14%), FT_PCT (−0.27%) or PF_36 (−0.31%), which are almost certainly inside the noise of the walk-forward estimate. Those columns are currently wired into `AGE_APPLY` on the strength of a sign alone. The adjustments are small enough to be low-risk, but they are not validated, and calling them "validated" in the table above overstates what was done.

## Aging, re-tested properly: five of eight applied columns were noise (August 2026)

The walk-forward table above marked ten metrics "YES" on the SIGN of the held-out change. Re-run with a **paired cluster bootstrap over players** (2,000 resamples; players recur across seasons, so resampling rows would give an interval far too narrow):

| verdict | metrics |
|---|---|
| **REAL** (95% CI entirely below zero) | MPG −3.31% [−4.68,−1.97] · PTS_36 −2.49% [−3.97,−1.06] · FTA_36 −1.43% [−2.49,−0.37] · TS_PCT −1.30% [−2.17,−0.42] |
| **noise** (CI spans zero) | AST_36 · FGA_36 [−2.49,+0.09] · FG3A_36 · TOV_36 · PF_36 · BLK_36 · FT_PCT · FG_PCT |
| **WORSE** (CI entirely above zero) | REB_36 · FG3_PCT · OREB_36 · DREB_36 · STL_36 |

`AGE_APPLY` had eight columns; **five of them are noise** (AST_36, FGA_36, TOV_36, PF_36, FT_PCT). Of the rate columns the engine actually consumes, only **MPG and FTA_36** survive, and those are now all that is applied. Direction re-verified after the cut: ages 20-23 gain +0.66 MPG / +0.10 FTA-36, ages 32+ lose −1.42 / −0.25, monotonic.

Note the tension worth keeping in view: PTS_36 and TS_PCT validate strongly, but the engine builds scoring from attempts and percentages and NONE of those clear significance individually. Aggregate efficiency ages measurably while its components do not — more power in the aggregate — so no efficiency ageing is applied rather than inventing a component split that the data does not support.

## Where the over-dispersion actually lives, decomposed

Splitting each team's score variance into a part shared with the opponent (covariance) and a part that is not:

| | V_independent | V_shared | team sd | corr |
|---|---|---|---|---|
| engine, full | 141.7 | 36.3 | 13.34 | 0.204 |
| engine, all injected noise off | 139.3 | 4.8 | 12.00 | 0.033 |
| **real** | **126.0** | **35.0** | **12.69** | **0.217** |

**The shared component is already right** (36.3 against 35.0), which kills the "just turn the shared noise down" plan — doing so would push the covariance BELOW target while barely touching the independent part. The entire error is that V_independent is 141.7 against 126.0, and the engine's floor with every knob at zero is 139.3, so **none of it can be reached by tuning injected noise.** It is the binomial floor.

Real basketball needs V_independent = 126 over 98.2 possessions, i.e. an effective per-possession variance of 1.286, while the directly measured per-possession variance is 1.4417 (script 146). The difference implies a small NEGATIVE serial correlation between a team's own possessions — about −0.0011 per pair, which would show up as a half-split correlation near −0.055. Script 147 measured −0.028 ± 0.019, so the required effect sits inside the confidence interval: **consistent, but the direct test is underpowered to confirm it.**

There is a reason it is hard to see directly, and it is the same artifact from earlier: demeaning a team by its own game mean manufactures exactly this pattern, so genuine own-deviation mean reversion and the fixed-effect artifact are observationally identical in the possession data. The game-level variance decomposition above does NOT suffer from that, and it puts the magnitude at 11%. The honest position is that the SIZE of the compression is well measured from game-level moments while its MECHANISM is unidentified.

## Mean reversion: strength DERIVED from the variance decomposition, and it lands

The compression magnitude is well measured (V_independent 141.7 in the engine against 126.0 real) even though the mechanism is not identified. Rather than tune a knob until the output matched, the strength was derived in closed form. Feeding a team's running deviation from its expected pace back into its scoring rate makes that deviation an AR(1), whose variance after n possessions is `sigma^2 (1 - e^-2nk)/(2k)`. The ratio to the independent `n*sigma^2` is `(1 - e^-2nk)/(2nk)`; setting it to 126.0/141.7 = 0.889 at n = 98 gives `2nk = 0.24`, so k = 0.00122 points per point of deviation, and as a multiplier on a 1.13 ppp offence **kappa = 0.00108** — a team 10 points ahead of its expected pace has its scoring rate cut by 1.1%.

Run at exactly that derived value, with nothing tuned afterwards:

| | margin sd | total sd | team sd | corr |
|---|---|---|---|---|
| off | 16.58 | 20.91 | 13.34 | 0.225 |
| **kappa = 0.00108** | **16.05** | **19.91** | **12.79** | **0.211** |
| real | 15.88 | 19.79 | 12.69 | 0.217 |

All four moments move to their targets together, which a single fitted constant has no right to do unless the underlying model is roughly correct. Doubling it to 0.0020 overshoots every one of them (15.37 / 18.94 / 12.20), so the derived value is not merely inside a flat region.

**Level is untouched**, which is the check every previous mechanism failed: points 226.12 -> 226.19, free throws 45.23 -> 45.24, anchor deltas −0.45/+0.31 -> −0.37/+0.30. `E[deviation] = 0`, so a mean-reverting multiplier is variance-only by construction, and the anchor needed no new term — the first mechanism in this project that did not break it.

### The derived value was aimed at the wrong target (script 148)

`MEAN_REVERT = 0.00108` was derived to make the engine's unconditional margin spread match the league's 15.88. **That target is wrong.** A forecast that knows the rosters should be NARROWER than the league-wide spread, because that spread also contains the variation between good and bad matchups — which the forecast is supposed to predict rather than sample. The right target is the model's own conditional error, and the ratio of predictive sd to residual sd is the thing to drive to 1.

Swept on 2023-24, confirmed on 2024-25 which was held out of the selection entirely:

```
tune 2023-24     logloss   brier    acc   MAE   pred sd  resid sd  ratio
  0.00000         0.6282  0.2195  0.654  11.30    16.88     14.21  1.188
  0.00108         0.6340  0.2224  0.628  11.38    16.18     14.25  1.135
  0.00300         0.6245  0.2182  0.639  11.33    15.05     14.19  1.060
  0.00550         0.6216  0.2169  0.654  11.28    13.69     14.09  0.972

test 2024-25 (never used for selection)
  off             0.5933  0.2039  0.712  10.83    16.94     13.66  1.240
  derived         0.5965  0.2051  0.707  10.80    16.24     13.63  1.192
  tuned 0.0055    0.5905  0.2023  0.697  10.71    13.77     13.59  1.013
```

**The derived value was worse than no reversion at all, on BOTH seasons.** The tuned value improves log-loss, Brier and margin MAE out of sample and lands the calibration ratio at 1.013. Two independent criteria — minimum log-loss and ratio → 1 — pick the same region, which is the reason to believe it rather than the single sweep.

The lesson is not that derivation failed; the algebra was right and the AR(1) reasoning predicted the shape of the response correctly. **The lesson is that a correctly derived quantity is still wrong if it is derived against the wrong benchmark**, and that the moment-matching check which looked so convincing (all four moments hitting their league-wide targets at once) was matching a distribution the model should not have been reproducing in the first place.

**AND THE PREDICTION GAIN IS NOT SIGNIFICANT.** The out-of-sample table above shows log-loss, Brier and margin MAE all improving, and that reads as a validated win. Paired per-game standard errors on the same 196 games with common random numbers say otherwise:

```
metric            off   0.0055     diff  paired se      t
logloss        0.5918   0.5889  -0.0029     0.0085  -0.34
brier          0.2031   0.2016  -0.0016     0.0035  -0.44
margin MAE    10.8626  10.7481  -0.1145     0.0992  -1.15
```

Every sign favours mean reversion and not one metric clears |t| > 2. At this sample size the accuracy question is simply unanswerable — 196 games cannot resolve a log-loss difference of 0.003.

So the justification for `MEAN_REVERT = 0.0055` rests **entirely on calibration**, which is measured directly and precisely rather than inferred from a noisy skill metric: the ratio of predictive sd to residual sd moves from 1.240 to 1.013, and the 80% intervals stop covering 87%. That is a real defect fixed. It is NOT evidence that the simulator predicts games better, and the two should not be conflated — which is exactly what the unpaired table invited. **Comparing two configurations without a paired standard error produces a confident-looking ordering out of pure noise**, the same failure mode as the sign-based aging verdicts earlier in this file.

## The shot clock: measured, valuable, and deliberately NOT built into the engine

Script 145 measured a large effect — within-player conversion falling monotonically from +0.055 pts/shot at 4-8 seconds to −0.101 at 20-24, a 0.156 spread across 18% of shots. Bigger than several mechanisms already in the simulator. Script 150 asks whether it should go in, and the answer is no:

| test | result |
|---|---|
| stable team property | **YES** — split-half r = **+0.973** (rebound conversion, the previous benchmark, was +0.755) |
| predicts efficiency above the team's own rate | **UNRESOLVED** — r = −0.256, 95% CI [−0.564, +0.115] on **30 teams** |
| reachable from the engine's state | **NO** — the fast tail is already transition (+13%) and the second-chance tail is already the putback loop |

Three reasons it stays out of `124`:

1. **The anchor already calibrates each team to its observed efficiency**, which contains whatever late-clock shots that team took. A late-clock penalty on top charges them twice — the same double-count that made the penalty free throws a SPLIT rather than an addition.
2. **Nothing in the engine's state predicts WHICH half-court possession runs long.** It would be sampled at random, so a mean-zero multiplier adds variance with no forecasting content — in a model whose predictive-sd/residual-sd ratio was just moved from 1.24 to 1.01. It would undo exactly the session's main gain.
3. **Test 2 cannot be settled at n = 30 teams.** The interval admits both a meaningful penalty and none at all. The point estimate leans the way the mechanism predicts and that is all it does.

A note on the test itself: the first version used a bare `|r| < 0.25` threshold, which the observed −0.256 cleared by 0.006 and so printed "both conditions pass — a build is justified". A hard cutoff on a correlation computed from **30 points** turned an unresolved result into a green light. It now reports a Fisher interval instead. **n is the number of teams, not the number of possessions**, and it is very easy to feel the weight of 255,000 possessions behind a statistic that actually rests on thirty.

Where it DOES belong is the play-by-play renderer (`134`), which already draws a possession duration and currently does so from league-average constants. Conditioning that draw on a team's clock profile — a property repeating at 0.973 — makes emitted play-by-play realistic without touching a single scoring rate.

### Where it went instead: per-team clock profiles in the renderer (151)

`data/parquet/team_clock_profile.parquet` — 390 team-seasons, 2014-15 through 2025-26. Each team gets a possession-duration multiplier against its season's league mean, shrunk toward 1.0 with a small prior (K=400 possessions) because within-season reliability is 0.973 and heavy shrinkage would be wrong; it matters only for partial seasons.

Range is 0.91 to 1.09, and the widest 2023-24 matchup is **11.4% apart in possession length** — where the renderer previously handed both teams identical duration distributions. Year-over-year persistence of the multiplier is **+0.575**, which is the number that matters, because the renderer uses the PRIOR season: a forecast cannot know how fast a team played in a game it has not seen. The pair is normalised to mean 1.0 so total game length, and therefore `_pace_scale`'s calibration, is untouched.

This changes no scoring rate. It is the one place the shot-clock finding can be used without double-counting an effect the anchor already contains.

## Harness: the eval loop now runs in parallel (149)

Every comparison in this file runs the same loop, and it has been running on one of eight cores while configurations executed back to back — a sweep that should take ten minutes was taking an hour. Games are independent, and the built Simulator is read-only, so a FORKED pool inherits it copy-on-write with no pickling and no rebuilding of projections (three minutes) per worker. Measured **4.38x** on six workers.

One correctness requirement, easy to miss: the serial harness drew each game's seed from a single generator IN VISIT ORDER, so under a pool the seeds would depend on scheduling and silently break the common-random-numbers property that every paired comparison here relies on. Seeds are now a pure function of `(base_seed, game_index)`, and the benchmark asserts that parallel results are bit-identical to serial before reporting any speedup.

## The intervals were never too wide — a seven-point measurement artifact

The evaluation has reported "80% interval coverage: 87.5%" for a long time, read as the simulator's bands being far too wide, and the conformal step that was supposed to fix it always returned **q = +0.00 and changed nothing**. Both had the same cause.

Player points are INTEGERS, and `p10`/`p90` drawn from a simulated distribution usually are too. Whenever a player's actual total lands exactly on a band edge, the conformity score `max(p10 − actual, actual − p90)` is exactly zero — and that happens to **14–15% of player-games**:

```
[80%] 73.3% below 0, 14.0% EXACTLY 0, 12.7% above   -> target quantile lands INSIDE the atom
[50%] 43.4% below 0, 15.3% EXACTLY 0, 41.4% above   -> same
```

Conformal prediction assumes a continuous score. With 14% of the mass piled on a single point, the empirical quantile lands on that atom, `q` comes out 0.00, and the method silently no-ops. Correcting the SCORE was not enough either: coverage in a discrete outcome is a STEP function, and the 50% band fell from 59.9% to 44.6% on a width change of 0.1 points, because shrinking by any epsilon drops the entire boundary atom at once. **No deterministic band can hit 50% on integer data.**

Jittering the OUTCOME by U(-0.5, 0.5) — the standard treatment for a discrete target scored against a continuous band — removes the atom and makes the guarantee meaningful:

```
80% band: raw 81.0% -> conformal 81.4%   (target 80%)
50% band: raw 52.8% -> conformal 51.6%   (target 50%)
```

**The bands were essentially calibrated all along.** The apparent 7-point over-coverage was counting band-edge ties as covered, and the reported 87.5% / 59.3% were never the right numbers to tune against. The headline metric now prints the corrected figure with the naive one in brackets, so the old value stays visible rather than quietly changing.

Worth stating plainly: this is the third time in this session that a headline number turned out to be a measurement artifact rather than a property of the model — after the score-feedback slope (a demeaning artifact) and the aging verdicts (sign of a held-out delta with no interval). All three were quantities that had been reported and reasoned about for a long time without anyone checking the measurement itself.

## The lineup sampler was silently giving bench minutes to starters

Chasing the excess-zeros diagnostic — the engine predicted P(0 points) = 66.5% for players projected 0-8 minutes against an actual 45.1% — led to a real bug, and the first check ruled out the obvious explanation. Running on ACTUAL minutes instead of projected made it WORSE (71.0%), so it was not a minutes-projection error: given a player's minutes, the engine was zeroing him out.

**`_lineups` drew five players without replacement using the occupancy curve as weights.** Weighted sampling WITHOUT REPLACEMENT does not reproduce its weights as marginal inclusion probabilities — heavy items crowd out light ones — and `LINEUP_PERSISTENCE = 35` multiplied the incumbent five by 36 first, making the crowding severe. Measured against assigned minutes:

| assigned | realized | ratio |
|---|---|---|
| (0, 8] | 2.51 | **0.421** |
| (8, 14] | 9.13 | 0.771 |
| (14, 20] | 14.41 | 0.854 |
| (20, 28] | 23.13 | 0.967 |
| (28, 48] | 36.53 | **1.071** |

A player assigned 6 minutes received 2.5. **Total team minutes still summed to exactly 240**, which is why nothing downstream ever flagged it — the minutes were silently redistributed from the bench to the starters. Every previous rotation check tested the total, not the distribution.

`occupancy` already returns exactly what a correct sampler needs: every column sums to five, every entry lies in [0, 1], so `M[:, s]` IS the marginal inclusion probability. Systematic pi-ps sampling hits those marginals exactly — cumulate to five and take the players crossed by u, u+1, ..., u+4; since no probability exceeds one, no player can be crossed twice, so exactly five distinct bodies come back. Ratios afterwards: **0.897 / 0.974 / 0.994 / 1.006 / 1.005**.

Two second-order fixes were needed. Players are ordered by total minutes before cumulating, because in an arbitrary order a tiny occupancy change flips a crossing between two unrelated players. And the five is held for `LINEUP_HOLD = 5` slots, because redrawing every thirty seconds churned 44 substitutions per team-game against a real 24.4; holding uses the window's MEAN occupancy, so the marginals stay correct while the substitution rate falls to ~25. Minutes accuracy is flat across hold lengths, so this trades nothing.

**Held-out effect, 242 games — the engine now beats the top-down baseline on both proper scoring rules for the first time:**

```
                acc   logloss   brier     margin  total
possession sim  0.711  0.5780  0.1972     10.43  14.49
Elo             0.698  0.6043  0.2088
Mode-1          0.723  0.5857  0.2002     10.47  14.65
```

Log-loss 0.5882 -> 0.5780 and Brier 0.2019 -> 0.1972, a larger move than anything else this session. P(0 points) for the 0-8 minute tier fell from 66.5% to 55.3% against an actual 45.1%. Player-points MAE got slightly WORSE (4.798 -> 4.836) and the PIT top bin is still light (6% against 10%), so the upper tail remains too fat — the bench players now on the floor are scoring somewhat too readily. That is the next thread, and it is a real residual rather than an artifact.

## A knob that was never connected to anything

Chasing the fat upper tail, an ablation of minutes jitter returned numbers **byte-identical** to the unablated run. That is not a small effect; it is no effect, and it exposed two stacked bugs:

1. `_jitter(self, M, rng, buckets=None, sd=MIN_JITTER_SD, ...)` — a **default argument**, which Python binds once when the function is DEFINED. Setting `S.MIN_JITTER_SD` afterwards changed nothing at all.
2. More importantly, `sd` only reaches the fallback Gaussian branch. Whenever the empirical minute pools are loaded — the normal path — spread is governed by `MIN_JITTER_SHRINK` and `sd` is dead code.

So the variance budget's line "minutes jitter: 0.00% of margin variance" was **not a fact about the model**. It was two layers of a knob not being wired to anything, reported as a finding. With the correct knob it does register: total sd 17.06 -> 16.78 and corr 0.220 -> 0.205 when removed. Small, but real, and it belongs to totals rather than margin.

**A general lesson for this file:** an ablation that produces an unchanged number is evidence the ablation did not run, not evidence the component does not matter. Identical to three or four decimal places should never be read as "no effect" — real ablations move a number somewhere.

## Mean reversion, split into margin and total

The corrected budget also showed that the single reversion constant had over-shot in one direction while fixing the other:

```
margin: predictive 13.59 vs realised 13.38  -> 1.015  (calibrated)
total:  predictive 17.06 vs realised 18.43  -> 0.926  (UNDER-dispersed)
```

A team's deviation from its expected pace splits into a part it SHARES with its opponent (both scoring above pace, which moves the TOTAL) and a part that differs (which moves the MARGIN). Reverting the raw own-deviation applies one strength to both, and the value that calibrates margin over-compresses totals. Splitting them — `MEAN_REVERT` on the differential half, `MEAN_REVERT_TOT` on the common half — decouples the two, and the sweep confirms the mechanism does what the algebra says: **margin sd stays 13.70-13.78 across every value of the total strength.**

| MEAN_REVERT_TOT | total ratio 2024-25 | total ratio 2023-24 |
|---|---|---|
| 0.0000 | 1.135 | 1.145 |
| **0.0030** | **1.013** | **1.033** |
| 0.0055 | 0.925 | 0.945 |

Set to 0.0030, which is best on BOTH seasons — including 2023-24, which was not used to choose it. At equal values the split reduces exactly to the old behaviour (`MR*diff + MR*common = MR*dev`), so this is a strict generalisation rather than a replacement.

## The residual after the sampler fix: usage is not conditioned on lineup context

Fixing the lineup sampler improved every game-level metric but made player-points MAE slightly worse (4.798 -> 4.831) and pushed PIT deviation from 0.076 to 0.126. Localising the PIT top-decile deficit by minutes tier shows it is **a bias, not a fat tail**:

| proj min | n | PIT top decile | mean pred | mean actual |
|---|---|---|---|---|
| (0, 8] | 220 | 8.2% | 2.3 | 2.4 |
| (8, 14] | 367 | 4.6% | **4.6** | **3.6** |
| (14, 20] | 599 | 4.5% | **7.1** | **5.7** |
| (20, 28] | 948 | 5.9% | 10.8 | 9.6 |
| (28, 48] | 1606 | 6.9% | 17.3 | 16.8 |

The engine over-predicts MID-ROTATION players by 1.0-1.4 points, and barely at all at the extremes.

**It is not the minutes.** The obvious hypothesis — that the sampler bug had been masking over-projected minutes for exactly these players — is wrong. Projected against actual minutes by tier runs 0.962 / 0.988 / 0.991 / 1.008 / 0.999, and the props engine's own points are well calibrated tier by tier (2.4 vs 2.3, 4.2 vs 4.1, 6.6 vs 6.5, 10.2 vs 10.4, 18.1 vs 18.0). The simulator consumes those same projected minutes, so the error is in points PER MINUTE.

The most likely mechanism is that **usage is not conditioned on who else is on the floor.** The engine allocates a possession among the five on court in proportion to each player's own season usage rate, normalised. But a reserve's measured usage was earned largely alongside other reserves; put him next to four starters and he should touch the ball far less than that rate implies. Before the fix he was barely on the floor, so the error was small; correcting his minutes exposed it. That also explains the shape — it bites hardest in the middle, where players split time between bench units and starter units, and vanishes at both ends where a player's team-mates are consistent.

This is a testable claim rather than a story: usage rates can be measured conditional on lineup strength directly from the stints, and the split-half machinery already used for rebound conversion (+0.755) and lineup chemistry (+0.02) will say whether the conditional rate repeats. That is the next thread, and it should be tested before anything is built — the last three mechanisms this project measured before building produced two "do not build" verdicts.

## The mid-rotation "over-prediction" was the evaluation harness, not the engine

The bias localised in the previous section — the engine over-predicting mid-rotation players by 1.0-1.4 points — was never a property of the simulator. It was created by the evaluation setup.

**Only 9.34 of 10.69 players per team-game carry a props projection**, and those players account for 217.8 of 241.3 actual team minutes. The harness dropped the rest and then rescaled the survivors to a full 240 team-minutes — inflating every simulated player by a factor of ~1.108. Worse, `occupancy` caps a player at one slot, so that surplus CANNOT go to starters and lands on the mid-rotation instead. That is exactly the tier signature that was observed: stars +3.5% relative, mid-rotation +24-29%.

Filling the roster with a leakage-safe fallback (the rate book's MPG, scaled by 0.83 so the roster lands on the real 241.3 rather than 246.0) changes the picture completely:

| proj min | sim bias before | sim bias after | props bias |
|---|---|---|---|
| (0, 8] | −0.10 | −0.23 | −0.10 |
| (8, 14] | **+1.05** | +0.65 | +0.49 |
| (14, 20] | **+1.39** | +0.63 | +0.26 |
| (20, 28] | **+1.20** | +0.15 | −0.03 |
| (28, 48] | +0.58 | **−0.99** | +0.19 |
| **overall** | **+0.886** | **−0.117** | +0.155 |

The tell that something was wrong with the measurement rather than the model was available the whole time and I did not use it: **the engine's team totals were anchored correctly (delta −0.39) while every player tier was over-predicted.** Those cannot both be true unless scoring is leaking to players outside the row set. Whenever a per-part error and a whole-total error disagree, the accounting is wrong somewhere, and that is a measurement question before it is a modelling one.

**The residual that survives is the opposite of the one I chased.** The engine now UNDER-predicts stars by about 1 point relative to props. And that reverses my earlier dismissal of lineup-conditional usage: script 152 found players with weak team-mates take MORE than the linear rule predicts (+0.0102 of share, ~4% relative, worth ~0.7 points on an 18-point scorer), and a star is by construction the highest-usage player in his own five. I had rejected that effect as "an order of magnitude too small" — **which was true against the 1.0-1.4 point artifact and false against the ~1 point real residual.** Dismissing a mechanism for being too small requires the target to be real.

It is still not being built. Fitting `share = u^g / sum(u^g)` returns **g = 0.80, the boundary of the search grid, with a −0.0% change in weighted MSE**. Per-cell share noise (sd ≈ 0.08 at 25 uses) swamps the objective entirely, so the fit resolves nothing about the functional form. A boundary solution with no objective improvement is a failed measurement, not a fitted parameter. The aggregate bucket table remains suggestive and the per-cell fit is simply not powered to confirm it.

### Held-out state after the harness fix (2024-25, 245 games, 150 sims)

```
                acc   logloss   brier      margin  total
possession sim  0.702  0.5817  0.1982      10.41  14.51
Elo             0.698  0.6034  0.2085
Mode-1          0.722  0.5866  0.2005      10.50  14.65

player points, IDENTICAL 4,625 rows:  sim 4.724   props engine 4.708
80% interval coverage 80.9% (target 80)   50% coverage 53.5% (target 50)
PIT histogram  8 9 10 10 11 11 11 11 10 8   deviation 0.100
```

The engine beats Elo on all three win-probability metrics, beats the top-down Mode-1 on both PROPER scoring rules (log-loss and Brier) while losing on raw accuracy, and has the best margin and total MAE of the three. Player points are now level with the dedicated props engine on identical rows (4.724 against 4.708), where before the harness fix they were 4.831 against 4.693 — the gap was mostly the inflated rosters, not the model. Interval coverage is essentially exact and the PIT histogram is close to flat.

Two honest caveats. Accuracy is worse than Mode-1 (0.702 vs 0.722) even though log-loss and Brier are better, which is the expected signature of a better-calibrated model that is less willing to commit near 0.5 — accuracy is a coarse metric and the proper scoring rules are the ones to trust. And a paired test earlier in this session showed differences of this size are NOT individually significant at n≈200 games, so the ordering against Mode-1 should be treated as suggestive rather than settled.

**The remaining real defect** is the lower tail: the engine still predicts P(0 points) = 55.5% for players projected 0-8 minutes against an actual 45.1%, and 15.1% overall against 12.1%. That survived every fix in this session and is not an artifact — it is the next thing to work on.

## The lower tail: systematic sampling gets the marginals right and the JOINT wrong

The excess-zeros defect survived every other fix, which was good evidence it was real. Decomposing it into volume and conversion located it immediately:

```
0-8 minute tier:  sim FGA 1.84 vs actual 2.05   (the MEAN is fine)
                  sim P(no FGA) 46.9% vs actual 26.7%   (the SHAPE is not)
```

The mean shot volume is roughly right while the engine gives low-minute players zero attempts nearly twice as often as reality. A player taking 1.84 shots over ~12.5 possessions on the floor implies a 15% share, and a binomial draw at that share would produce P(no FGA) near 13%, not 47%. Something upstream was over-dispersing it.

**Two candidates were eliminated by measurement before the real one was found.** `USE_SHRINK` from 0.72 all the way to 0.0 moved P(no FGA) by 0.3 points — the usage pool is not the cause. `MIN_JITTER_SHRINK` to 0.0 moved it from 46.9% to 39.7%, real but explaining only a third of the gap.

The cause is the **systematic pi-ps sampler installed earlier in this session**. It delivers exactly the right marginal inclusion probability in every slot — which is what it was built to fix, and it does — but with a FIXED offset `u` the JOINT distribution across slots is severely correlated: whether a marginal player is on the floor at all depends on where a single number falls, so he is either in for long stretches or out for the entire game. Minutes are correct on average and BIMODAL across simulations. `LINEUP_DRIFT` had been set to 0.0 precisely to control substitution churn, which maximised the correlation.

Drifting the offset decorrelates the draws without touching the marginals:

| LINEUP_DRIFT | P(no FGA), 0-8 | overall P(0 pts) | subs/team-game |
|---|---|---|---|
| 0.00 | 46.9% | 13.8% | 31.2 |
| **0.06** | **41.0%** | **12.1%** | **31.7** |
| 0.35 | 36.0% | 9.9% | 32.6 |
| *actual* | *26.7%* | *12.1%* | *24.4* |

Churn is driven by the occupancy curves rather than by `u`, so the substitution count barely moves. At 0.06 the overall zero rate lands exactly on the observed 12.1%, and the held-out player distributions reach the best calibration recorded in this project:

```
PIT deviation  0.100 -> 0.064     histogram 8 11 10 10 10 11 11 10 10 9
coverage       80.1% / 52.7%      (targets 80 / 50)
zero rate by tier   (8,14] 31.5 vs 32.7 | (14,20] 14.6 vs 14.3 | (20,28] 4.8 vs 4.1
```

**The game-level effect is NOT resolvable and should not be claimed either way.** The two full evaluations appeared to show log-loss worsening 0.5817 -> 0.5893, and a paired run with common random numbers on the leaner pipeline showed the OPPOSITE, 0.5877 -> 0.5822, with margin MAE better at t = −2.26. The pipelines differ (the official eval applies in-season rate overrides; the lean one does not) and both differences sit near the paired standard error of 0.0071. So the honest reading is that game-level prediction is unchanged within noise, and the change is adopted on the strength of the player-level calibration, which is large and unambiguous.

Note how close this came to being reported as a trade-off. A single pair of unpaired evaluation runs said "player calibration improved, game prediction degraded", which is a tidy and completely unsupported story. **Two configurations differing by less than a standard error will always order themselves somehow.**

**Residual:** the lowest tier is still 41.0% against 26.7%. The joint correlation is reduced, not removed, and a player projected 6 minutes still misses entire simulated games more often than he should.

## League drift: the rate book lags the league it projects into

Chasing a free-throw excess (+2.9 FT/game against the sample's own actual) led to a general problem rather than a free-throw quirk. The engine reproduced its inputs faithfully — expected 45.23 FT from the rate book, produced 45.49 — so the error was in the projections, and it was not confined to one column:

```
rate        prior book   in-season   verdict
FG2A_36          1.059       1.001    fine after updating
FG3A_36          0.915       0.958    4.2% UNDER
FTA_36           1.063       1.031    3.1% over
OREB_36          0.914       0.948    5.2% UNDER
AST_36           0.976       0.977    2.3% under
PF_36            1.045       1.035    3.5% over
```

These are the familiar league trends — three-point volume and offensive rebounding rising, fouls and free throws falling — and a Marcel projection that regresses toward a multi-year mean necessarily lags them. Free throws are the worst case because league volume genuinely oscillates (47.1, 43.4, 43.3, 47.0 across consecutive seasons, driven by officiating), so regressing toward a multi-year mean lands nowhere near the target season.

Script 153 scales every player's rate by `league level observed so far / league level the rates imply`, which moves the aggregate while leaving the SPREAD across players untouched — the part the book is good at. Each game uses only games played strictly before its date, and early-season factors are shrunk toward 1.0 because a handful of games estimates a league level poorly.

**The first version double-corrected and broke two columns that had been fine.** It measured the gap against the prior-season book, but the pipeline applies in-season updates BEFORE this correction and those already remove most of the drift. Applying a book-derived factor on top pushed FG2A from 1.005 to 0.960 and TOV from 1.013 to 1.046. The factor has to be the RESIDUAL bias of the rates the engine actually uses, not of the rates it starts from — the same double-count that made the penalty free throws a split rather than an addition. Rebuilt that way, the factors shrink from (FG3A 1.079, FTA 0.972) to (1.029, 0.996) and the correction lands:

```
rate        in-season   + drift   ins/act   drift/act
FG3A_36         72.85     75.19     0.961       0.992
FTA_36          43.87     43.49     1.013       1.005
OREB_36         21.51     22.16     0.946       0.975
AST_36          51.17     52.02     0.968       0.984
PF_36           38.25     38.01     1.034       1.028

total absolute deviation from 1.0:  0.199 -> 0.131  (-34%)
```

**Held out, paired on identical games and seeds:** interval coverage improves (80.1/52.7 -> 79.9/51.8, both closer to target), margin MAE 10.52 -> 10.48, player MAE essentially unchanged (4.715 -> 4.723), and log-loss moves +0.0064 — about one paired standard error (~0.0071 measured earlier), so within noise. Adopted on the strength of the directly measured league-rate fidelity, NOT on a claimed prediction gain. Two columns (TOV, FG2A) remain slightly worse than leaving well alone, which is the honest cost of a single league-wide factor per rate.

### Drift replicates in aggregate and NOT column by column

The obvious refinement — apply the correction only to columns where it demonstrably helps — is wrong, and generating in-season rates for 2023-24 (script 131) made it possible to show why. Validated on both seasons:

```
              2023-24            2024-25
FTA     1.027 -> 1.005     1.013 -> 1.005     consistent
AST     0.957 -> 0.982     0.968 -> 0.984     consistent
PF      1.034 -> 1.026     1.034 -> 1.028     consistent
TOV     1.020 -> 1.000     1.013 -> 1.028     FLIPS
OREB    0.996 -> 1.012     0.946 -> 0.975     FLIPS
FG2A    1.010 -> 0.993     1.005 -> 0.985     FLIPS
DREB    0.985 -> 0.973     0.992 -> 0.993     FLIPS

total absolute deviation:  0.177 -> 0.117 (-34%)   0.199 -> 0.131 (-34%)
```

**The aggregate improvement replicates almost exactly (-34% in both seasons); the per-column detail does not.** Four of eight columns change sign between seasons. Dropping the columns that looked bad on 2024-25 would have removed TOV — which is one of the biggest WINNERS on 2023-24 — and kept the selection fitted to one season's noise. All eight columns stay.

This also resolves the TOV puzzle from the 2024-25 numbers. The factor is computed league-wide over actual minutes and applied to projected rosters with projected minutes; those populations differ, and the residual disagreement is roughly the size of the per-column noise. It is not a bug to be chased, it is the resolution limit of a single league-wide factor per rate.

**Box realism now reported in the official evaluation** (section 3b), because the free-throw investigation was conducted on the lean harness, which omits in-season updating, and reported a 6.7% free-throw excess that the real pipeline does not have. On the official pipeline every category sits within 1-4%:

```
FTA 1.024   FGA 1.004   FG3M 0.971   TOV 1.042
AST 1.002   REB 1.012   PF 1.019
```

**Anchor re-verified with drift applied**: −0.54 / +0.05, predictive-to-residual ratio 1.006, points 226.9 against an actual 226.8. The correction changes every rate the closed form consumes and did not move the level.

## The lowest tier: right diagnosis, wrong instrument

The remaining defect after the drift work was the bottom minutes tier: P(0 points) 50.9% against an actual 44.4%, and P(no FGA) 41.0% against 26.7%.

The diagnosis was sound. `LINEUP_DRIFT` is a random walk on the systematic-sampling offset, and a walk of step 0.06 needs roughly (1/0.06)^2 ~ 280 moves to traverse the unit interval while a game offers only ~19 redraw windows. So `u` barely travels, a marginal player's inclusion stays correlated from tip-off to buzzer, and his minutes come out correct on average but bimodal across simulations. Replacing the walk with a golden-ratio low-discrepancy sweep makes `u` cover the interval in one step per window.

**It works exactly as predicted on the tier it was aimed at, and is still the wrong thing to do:**

```
zero-rate gap vs actual, by projected-minutes tier
              (0,8]   (8,14]  (14,20]  (20,28]  (28,48]
sweep 0.0      +6.4     -2.6     -1.1     +0.7     +0.0
sweep 0.618    +2.3     -9.9     -5.4     -0.5     -0.1
```

The bottom tier improves from +6.4 to +2.3. The middle collapses from −2.6 to −9.9 and from −1.1 to −5.4, and those tiers hold **four times as many players**. Weighted by tier size the sweep more than doubles total error. Reverted.

What this actually establishes is a SHAPE mismatch that a single global mixing rate cannot fix: the bottom tier carries too much minutes variance while the middle carries too little. Sweeping reduces variance everywhere, so it can only ever trade one against the other. The instrument for this is the per-bucket empirical minutes pools (`MIN_JITTER`), which already vary by projected-minutes band and are the only place the two tiers can be moved in opposite directions.

Recorded rather than pursued, because the bottom tier is 270 of 4,307 scored player-games — 6% of the rows carrying the smallest scoring weight in the model.

## The play-by-play renderer was missing half the fouls

The end goal for this project is a full ESPN-style box score and play-by-play for any matchup, so the RENDERER's box realism matters as much as the engine's. Measured across 60 simulations of one matchup against league averages, it was not close:

```
stat      before   after    real     note
PF         0.473   0.986    37.2    booked fouls ONLY on shooting fouls
STL        0.728   0.945    14.6    credited steals only on LIVE-ball turnovers
FTA        0.922   0.969    43.3    no penalty free throws at all
REB        1.152   0.999    88.5    no team rebounds (out of bounds / deadball)
FGA        1.007   1.005   178.7
FG3A       0.992   1.004    76.3
poss       1.009   1.003   199.0
```

Four mechanisms present in the possession engine had never been ported to the renderer: non-shooting fouls, the penalty, team rebounds, and unconditional steal crediting. **Fouls at 0.47 of the real rate is the kind of error that a points-focused evaluation cannot see at all** — the scoring was fine the whole time, which is why it survived. Adding box realism to the official evaluation (section 3b) is what made it visible for the engine; the renderer needed its own measurement because it is a separate implementation of the same game.

One deliberate divergence: the renderer needs a SMALLER free-throw trim than the engine (`PBP_FT_TRIM = 0.066` against `PEN_FT_TRIM = 0.137`). It accumulates team fouls over real periods rather than over an approximated possession index, so it reaches the penalty less often and its penalty branch supplies fewer shots. Porting the engine's constant unchanged put free throws at 0.909; the two implementations genuinely need different values.

Remaining renderer gaps, all small and recorded rather than chased: BLK 1.12, AST 1.03, TOV 0.91, OREB 0.93, PTS 1.02.

## Any two teams: the matchup driver (154)

The stated end goal for the project is to match up any two teams and get a full play-by-play plus a complete box score with every player's line. Everything before this could only REPLAY a scheduled game — `134 --game 0022400061` keys the roster, the projected minutes, the Mode-1 anchor and the measured pace off a GAME_ID, and a hypothetical matchup has none of those. Script 154 builds the three missing inputs from team identity alone.

**Roster.** Recency-weighted minutes (8-game half-life over the last 20 games) for whoever has actually been playing, rescaled to a legal 240 team-minutes AFTER inactives are removed — so ruling a starter out redistributes his minutes to the players who absorb them rather than leaving the team five men short.

**Strength.** The engine takes the level top-down on purpose: a bottom-up sum of player rates loses to a team rating, which is the reason the anchor exists. With no Mode-1 row to look up, it is solved directly from results as a ridge least-squares, `points_for = mu + OFF(team) − DEF(opponent) + HCA*home`, so opponent adjustment falls out of the fit. Fitted on the first 60% of a season and scored on the rest:

```
2024-25                     margin MAE  total MAE  margin bias
standalone ratings               11.49      14.82       +0.03
Mode-1 (what it replaces)        10.99      14.60       +1.38

2023-24
standalone ratings               11.73      15.77       +0.66
Mode-1                           11.68      14.26       +0.93
```

Within half a point of a purpose-built model on margin, comparable on totals, better on bias in 2024-25 — and it works for matchups that never happened, which the lookup cannot.

**Two display bugs found by reading the output**, both invisible to any metric. Player names were rendered with `split()[-1]`, so anyone called "... Jr." appeared in the box score as literally **"Jr."**. And every foul line was tagged to the OFFENSIVE team while naming the defender who committed it, so "OKC Hartenstein Foul" printed as "DEN Hartenstein Foul". Neither affects a single number the model produces; both would be immediately obvious to anyone reading a box score, which is the point of building the thing.

## Rosters, rookies, and a play-by-play that ignored team strength

Three defects surfaced from one observation — that Jaylen Brown was still on Boston.

**1. Rosters were inferred from box scores.** `154` derived a roster from who last PLAYED for a team, which learns the rotation correctly and the roster wrongly. Our game data ends 2026-06-13, so the entire offseason was invisible. Script `156_pull_rosters.py` now pulls the real roster (586 players, 30 teams, 86 rookies) and refuses to write a partial file, because a short roster would receive enormous minutes from the 240-minute rescale.

**2. The rookie priors had never been used.** Script 141 built draft-slot priors so a player with no NBA history gets a plausible rate profile, and 124 wires them in through `RateBook.__missing__`. But that lookup needs a draft slot from the bio archive, and a rookie who has never played has no row in `player_games` either — so the roster builder never proposed him and the priors sat unused **for exactly the players they were built for**. The roster pull carries `HOW_ACQUIRED` ("#40 Pick in 2026 Draft"), which supplies slots for 67 players the archive does not know.

**3. THE PLAY-BY-PLAY WAS NEVER ANCHORED.** The renderer had no `team_scale` at all: it played out the rate book and ignored team strength, so the projected score displayed beside it was decorative. A matchup projected OKC 120.5 / DEN 113.5 rendered as DEN 138 — OKC 119. This is the product's headline output and it had no connection to the ratings the site shows next to it.

Porting the engine's closed-form anchor solve fixes it, and the same mechanism handles the roster staleness for free. The anchor calibrates against a REFERENCE roster and applies the BPM difference to what is actually playing — built for injuries, but an offseason is the same problem with last season's roster as the reference. Roster turnover is worth **2.10 pts/100 on average and up to 7.07** (WAS +7.07, CHA −4.03, BOS −3.94), so leaving the reference unset applies a stale rating at full strength.

**The calibration hunt found a real bug under two tuned constants.** Anchoring left a +9.3 point bias. Two passes of a scale correction removed only half of it and then saturated — a much larger factor moved the bias by 0.4 points, which meant the scale was not the lever. It was the possession COUNT: 113 against a pace target of 102.9.

The cause was a default. `padj` reads each on-court player's pace effect with `.get(pid, 0.0)`, while the centring term subtracts the mean for ten average players — so **every player missing from the pace model leaves the sum short by one mean and pushes the adjustment negative**, shortening possessions and manufacturing more of them. It was invisible while rosters came from played games, because everyone in such a roster has a pace estimate by construction. On a current roster full of rookies and new signings it ran the game 8% fast. Defaulting to the league mean took possessions from 1.078 of pace to 1.027 and the bias from +9.3 to +5.5 with **both tuned constants back at 1.0**.

A residual +1.7 remains on the anchored path only, absorbed by one constant that cannot touch an unanchored render — verified by re-measuring box realism afterwards (points 1.004, possessions 0.988, unchanged).

**The lesson is the one this file keeps recording.** A constant that removes half an error and then stops responding is not a calibration, it is a symptom. Two of them stacked would have hidden a genuine bug behind plausible-looking numbers, and the tell was the saturation, not the size of the residual.

## The simulator becomes interactive: a browser port, and what it cost

The product goal is to pick two teams, get a box score and a play-by-play, then run 10/100/1000 more and read each player's average, minimum and maximum. The Python engine reads parquet and takes about a second a game, so it cannot run in a page, and precomputing 435 matchups x 1000 simulations is not feasible either. What IS portable is the loop.

`web/sim.js` re-implements the possession loop in the browser: usage allocation, the four-way outcome mix, offensive-rebound continuation, fouls and the penalty, assist/steal/block credit, and mean reversion split into its margin and total components. **1000 simulations run in about 220ms.** It deliberately omits refinements whose data is too large to ship or which move totals by well under a point — shot zones, defender matchup affinity, height and weight, cold start, transition and endgame shot selection.

**A second implementation of the same model is a liability unless it is checked against the first**, so `157_validate_js.py` runs both on identical matchups under macOS's JavaScriptCore and compares what a user actually reads:

```
team score    mean |diff| 1.31 pts   max 3.79
player points mean |diff| 0.82 pts   max 3.01   (72 rotation players)
```

Three bugs the validation caught, none of which would have been visible from the output alone:

**Minutes were halved.** A player was credited only for possessions in which his own team had the ball, so a 34-minute starter reported 13.8. Each iteration of the loop is one possession for EACH team.

**A flat rotation nobody plays.** Taking twelve projected rotation players and rescaling them to 240 team-minutes multiplied everyone by 0.77, so a 36-minute star came out at 27. Twelve projections sum to ~311 minutes; the fix takes players in order until their own projections ADD UP to a game, which keeps each player's projection and lets the rotation end where a real one ends.

**Stars missing entire games.** The engine drifts its sampling offset slowly because its play-by-play must show a believable substitution count. Ported directly, that left a star with no minutes in 8.5% of simulations. The browser port shows no substitutions, so the correlation buys nothing there and the offset is drawn fresh each window.

The level is solved EMPIRICALLY in the browser rather than by porting the engine's closed form: run warm-up games, see what this implementation actually produces, scale to the projection. Three passes, because scaling a make probability does not move points linearly — one pass under-corrects and two still left some matchups 4 points high. Being self-correcting, it means the port's own offsets cannot drift the displayed level even if the two implementations diverge further.

## A one-rebound Jokić game is fine; his average is not

Prompted by a 1-rebound minimum showing up in 100 simulations of DEN vs OKC.

**The tail is right.** Jokić has 757 career games of 20+ minutes in the archive and exactly one with a single rebound (0.13%). Across all comparable seasons — 9.5+ rebounds in 28+ minutes, 12,909 games — 13 have one rebound or fewer, 0.10%. The simulator produces it at **0.10% for 20+ minute games**, matching exactly. Seeing one in a run of 100 is roughly a 1-in-6 event, so it is not evidence of anything wrong.

The mid-tail is modestly fat: 2 or fewer at 0.50% against a real 0.35%, 3 or fewer at 1.74% against 1.15%. About 1.5x too generous with bad rebounding nights.

**The mean is the real problem.** Jokić averages 12.9 rebounds in 2025-26 (13.3 per 36). The engine gives him 10.5 in 32.5 minutes (11.6 per 36) and the browser port 9.7 (10.7 per 36). Testing whether the allocation reproduces the rate it was GIVEN, across four matchups:

```
input REB/36     n    input   simulated   ratio
(0, 4]           7     3.46        3.41   0.985
(4, 6]          14     5.23        5.25   1.003
(6, 8]          10     6.77        6.73   0.994
(8, 10]          1     8.41        8.71   1.035
(10, 20]         4    11.63       10.16   0.874
```

Everyone below ten rebounds per 36 comes back within half a percent of their input. **Elite rebounders come back at 0.874 of theirs.** Rebounds are handed out in proportion to each on-court player's rate, and a rate earned across all of a player's lineups does not transfer to a share within one lineup — the same failure the usage-context work found on the scoring side, where a reserve's usage does not survive being put next to four starters.

Only four players sit in that top bucket, so the size of the effect is not settled even though its direction is consistent across matchups. Worth fixing before the site's box scores are trusted for individual rebounding, since it is the same 12-13% for every elite rebounder and it always points the same way.

## Every allocated stat was compressed toward the middle (158, 159)

Chasing Jokic's rebounds found a defect in every counting stat the engine
produces, not just his. Rebounds, assists, steals, blocks, fouls and the
possession itself are all handed out by drawing one of the five on court in
proportion to their per-36 rates. **A rate is earned across the mix of team-mates
a player actually plays with; renormalising it inside one specific five pulls
everyone toward the middle of that five.** Script 158 measures it:

```
stat    q1 (low)   q2      q3      q4    q5 (high)
REB       0.984  0.966   0.926   0.934     0.874
AST       1.159  1.110   1.063   1.032     0.966
STL       1.033  1.011   1.021   0.920     0.864
BLK       1.400  1.267   1.253   1.145     0.986
PF        1.313  1.107   1.046   0.946     0.898
FGA       0.960  0.985   0.958   0.936     0.884
FTA       1.055  0.988   0.975   0.957     0.880
```

The top quintile came back 11-14% short on every stat and the bottom quintile up
to 40% long. It is the same mechanism the usage-context work found on the
scoring side, and it had been invisible because team totals were right the whole
time — the split was wrong, not the sum.

**The fix is a fixed point, not a model change.** Script 159 scales each
player's allocation weight by `target / observed` and iterates. Because a share
is normalised within the lineup, this moves only the SPLIT and can never change
a team total, so nothing above it — the anchor, the level, the box realism — is
at risk by construction.

**The first attempt stalled at 13% error with every factor drifting away from
1.0** (steals to 0.63, rebounds to 1.34). The targets were unreachable: if a
team's total for a stat differs from the sum of its players' rate-implied
targets, no reallocation can reach them, and the iteration was trying to fix a
TOTAL with weights that can only move a SPLIT. Rescaling the targets to the
observed total gives a reachable fixed point, and it then converges properly —
9.89% -> 6.22% -> 4.39% -> 3.31% -> 2.89%.

Afterwards, seven of eight splits sit within 4% of correct where they had been
11-14% short:

```
stat   team total   share q1   share q5
REB         0.932      1.022      0.978
AST         1.037      0.990      1.018
STL         0.960      0.980      0.964
FGA         0.937      0.974      1.003
PF          1.063      1.045      1.005
```

**The residual is team totals, and it is NOT the engine's fault.** Measured
against real league averages the engine's totals are right (rebounds 0.999,
shots 1.005); measured against the summed rate book they are 6% low. The book
over-states, because per-36 rates summed across twelve players at 240 minutes
imply more rebounds than a game contains. Chasing fidelity to the book here
would make the engine WORSE against reality, so it is deliberately left alone.

**What remains for Jokic specifically**, and it is three compounding losses
rather than one bug: the book projects 12.58 REB/36 against his actual 13.3
(Marcel regression, −5.5%), the engine's team rebound total runs 42.4 against a
real 44 (−5.8%), and the rotation gives him 32.5 minutes against a real 34.8
(−6.6%). Each is individually defensible; together they are the 17% gap between
10.7 simulated and 12.9 actual.

## Two rebound errors that had been cancelling

After the share calibration, rebounds were still short in the matchup driver (82.0 against a real 88.5) while the official evaluation showed them fine (1.012). Both were true, and the reason is that two errors were offsetting.

**Missed free throws are live and get rebounded; the engine ended every trip to the line.** Measured in the play-by-play: 4.82 missed final free throws a game, of which 4.81 are rebounded — 4.6% of all rebounds, simply absent. Offensive rebounds off a free throw are much rarer than off a field goal, 10.7% against 25.0% measured across 6,318 of them, because the defence is already lined up; that ratio is now a constant rather than an assumption.

The first attempt at this recovered only 0.8 rebounds a game instead of 4.6, because the branch returned unconditionally once a trip scored. **Most missed last free throws follow a made one**, so only the all-missed trips ever reached the board — the common case was still being dropped.

**Team rebounds were under-booked at less than half their real rate.** In the play-by-play they carry a team id rather than a player's ("Hawks Rebound") and are **15.8% of all rebound events** — 16.56 of 104.59 a game, leaving 88.03 for players against a box-score 88.47. The engine used 0.072. It cannot use 0.158 directly either, because it generates 100.4 rebound opportunities a game rather than the league's 104.59: it does not model the deadball situations that produce many team boards. Applied to its own opportunity count the correct share is **0.119**.

At 0.072 players were over-credited by almost exactly what the missing free-throw rebounds took away, so the total looked right (1.012) while both halves were wrong. Fixing one alone made it worse — adding free-throw rebounds pushed the total to 1.053 — which is what surfaced the second error. **A total that matches is not evidence that its parts do.**

```
                    before   +FT rebounds   +team share   real
team rebounds        89.5        93.2          88.48      88.47
```

Jokic across the three fixes — share calibration, rotation minutes, rebound mechanics — goes from 10.5 to 12.0 rebounds in 34.3 minutes, against a real 12.9 in 34.8. The remaining 7% is the rate book projecting 12.58 per 36 against his actual 13.3, which is Marcel regression doing its job on a forecast rather than an engine defect.

Held out afterwards: rebounds 1.000, shots 1.006, assists 1.004, fouls 1.021, free throws 1.028, interval coverage 80.2% and 52.4% against 80/50 targets.

## Minutes: the marginal player should absorb the overflow

Rosters were filled until the cumulative projection crossed 240 minutes and then everyone was rescaled to fit. Rosters summed to 251.4, so every player was multiplied by 0.955 and a 34.0-minute starter was assigned 32.5 — **the overflow created by admitting the last man was charged to the whole rotation.** Filling to exactly 240 and letting the marginal player take what is left restores every projection: slot 1 now gets 34.1 against a book 34.0, and the tail absorbs the remainder.
