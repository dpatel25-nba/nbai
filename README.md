# NBAI — NBA Ratings & Prediction Engine

A local NBA data pipeline and predictive-modeling project, plus the **NBAI** website
that surfaces its ratings and predictions.

## Layout
- `scripts/` — the pipeline: scrapers (`01`–`02`), validation/audit (`03`, `12`),
  Layer-2 parquet builders (`10`, `20`, `21`), the Elo model + backtest (`11`),
  and the website exporter (`export_web.py`).
- `data/` — raw JSON archive + clean parquet tables + features (git-ignored, built locally).
- `web/` — the static NBAI website (`index.html` + generated `data.js`). See `web/DEPLOY.md`.
- `docs/` — the modeling plan and research.

## Data architecture (three layers)
1. **Raw JSON** — untouched API responses (archive).
2. **Clean parquet facts** — `games`, `player_games`, `team_games`, `pbp/`.
3. **Features** — leakage-safe, model-ready (e.g. Elo predictions).

## Refresh the website
```
python scripts/export_web.py     # regenerates web/data.js from the model
```
Then open `web/index.html`, or push to deploy (see `web/DEPLOY.md`).
