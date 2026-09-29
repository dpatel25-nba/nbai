# Season impact fallback correction — 2026-09-29

The exporter previously queried only 2025–26 impact rows, then assigned -1.5
to everyone absent. This discarded usable history for eight rostered players,
including Haliburton (+1.8663 from 2024–25), Irving and Lillard.

## Selection rule

Preserve the latest finite pre-target-season BPM3 within the same three-season
window used for player rate inputs. Keep current-season evidence unchanged,
including genuinely negative ratings. Never use target-season or future rows.
Absent or older evidence still uses -1.5, with separate no-history/stale-history
labels. Duplicate player-season evidence fails explicitly. Each player carries
impactSource, impactSeason, impactLastAvailableSeason and impactSeasonGap.

All 586 rostered players were audited against the source table: 489 retain
previous-season evidence, eight carry recent history, 86 have no usable history,
and three have history outside the window. No new injury-return or impact-aging
penalty was fitted. Historical carry-forward is a transparent assumption;
existing age adjustments to individual box-score rates remain unchanged.

## Full-league diagnostic

30 seeds, 202627–202656; 1,230 games and all 30 teams per scenario, with original
pre-fix inputs, corrected original rosters, and corrected Haliburton-to-NYK rosters.
110,700 simulated games in total. Seed-independent matchup calibration was cached
separately for each input/roster scenario. The engine and warm-up method were unchanged.

| Team | Corrected original wins | Corrected transfer wins | Paired mean change | SE of change |
| --- | ---: | ---: | ---: | ---: |
| IND | 33.33 | 31.13 | -2.20 | 0.76 |
| NYK | 46.90 | 53.40 | +6.50 | 1.02 |

Indiana's target strength now drops 1.270 points per 100 possessions when
Haliburton leaves; New York's rises 2.891. Indiana still gains wins in 9/30
individual seed pairs (range -14 to +5). The UI therefore labels the win delta
as a single-season comparison, not average trade impact. These diagnostics test
simulation response, not historical prediction accuracy or causal trade effects.

Validation: five evidence-selection tests; full-league engine invariants and
Haliburton transfer regression; all-player source audit; browser workflow check.
Detailed all-team aggregates are in season-impact-fallback-2026-09-29.json.

Reproduce from the research workspace:

```sh
venv/bin/python -m unittest discover -s tests -p test_season_impact.py
venv/bin/python scripts/226_export_season_sim.py
node tests/test_season_engine.cjs
node scripts/227_audit_season_impact.cjs 30 [optional-before-payload.js]
```

The exporter requires cached schedule PDF/receipt, roster/model parquet inputs
and web_sim.json. Refreshing or rebuilding inputs may change results; their
hashes are embedded in season-data.js. The production repository retains its
existing Python/game model version; reproduction against a different engine
revision must be identified by those source hashes.
