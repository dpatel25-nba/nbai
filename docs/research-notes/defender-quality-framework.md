---
name: defender-quality-framework
description: "Our custom multi-signal, opponent-adjusted defender-quality metric (points saved/100) and how it feeds \"defensive pressure faced\""
metadata: 
  node_type: memory
  type: project
  originSessionId: 052674ad-d480-4e3c-940d-00c046841f89
---

**Goal (user, 2026-07-18):** build OUR OWN in-depth defender-quality metric = points saved per 100 possessions on defense; opponent-adjusted, multi-signal, with uncertainty. Feeds the "defensive pressure faced" metric (possession-weighted avg of defender quality over who guarded an offensive player, from matchup data). See [[north-star-vision]].

**Architecture — two-layer blend (RAPTOR/EPM style):**
- Layer B backbone = **D-RAPM** (defensive regularized adjusted plus-minus): ridge regression on reconstructed stints, opponent+teammate adjusted by construction. High signal, noisy.
- Layer A prior = regression predicting D-RAPM from observable defensive signals (stable, every season).
- Blend by possessions (empirical-Bayes): `DefQ = (poss·D-RAPM + K·BoxPrior)/(poss+K)`.

**Prior signals (each opponent-adjusted, in pts/100):**
1. Matchup suppression (2017-18+): per matchup, credit = offender's expected pts/100 × (p/100) − pts allowed. Compares to offender's OWN baseline → fixes the "guarding Curry looks bad" confound.
2. Rim protection (2013-14+ tracking, have `defendedAtRimFieldGoals`): opp rim FG% vs league × volume.
3. Perimeter shot defense (post-scrape `playerdashptshotdefend`): opp FG% by distance vs expected; pairs with our xPTS model.
4. Disruption events (steals/blocks/deflections 2015-16+/forced TOs): point value CALIBRATED FROM OUR PBP (measure actual possession-value swing per event type), not borrowed constants.
5. Defensive rebounding (DREB + contested DREB).
6. Foul cost (negative): FTs allowed, calibrated from pbp.

**Two commitments that make it good:** opponent-adjust everywhere (else stoppers look bad); multi-year + shrinkage (single-season D-RAPM misleads).

**Coverage:** 2013-14+ = D-RAPM + rim + events + rebound + foul (full, lighter on-ball). 2017-18+ = add matchup suppression + perimeter (complete).

**Validation:** predicts team's FUTURE defensive rating out-of-sample; high DefQ correlates with opponents' reduced efficiency; face validity (Gobert/Draymond/Bam/Giannis/Jrue on top); bake-off vs simple DBPM.

**Build order:** stints → D-RAPM → box/event prior → blend, then matchup/perimeter layers post-scrape. Stint reconstruction from pbp is the keystone (shared with matchup engine + RAPM-WAR). Buildable now: event-value calibration, rebound/foul, prelim rim protection, and stints/D-RAPM (engineering-heavy). Blocked: matchup + perimeter (post-scrape).
