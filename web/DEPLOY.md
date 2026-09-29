# Deploying NBAI (first-timer guide)

The site is a **static site**: plain files in `web/` (`index.html` + `data.js`). No server
needed. `data.js` is generated from the model by `scripts/export_web.py`.

## Preview it locally (no tools)
Just open `web/index.html` in your browser (double-click it). It loads `data.js` from the
same folder. To refresh the numbers after the model changes:

```
python scripts/export_web.py     # regenerates web/data.js
```

### Refreshing the Simulator tab

`export_web.py` only *reads* the simulator payload; it does not regenerate it,
because doing so runs the possession engine 30 times. Two files feed that tab and
both are snapshots of whatever the engine was when they were written:

```
python scripts/156_pull_rosters.py --season 2026-27   # who is actually on each team
python scripts/155_export_sim.py           # ~4 min: ratings, per-team boxes, one play-by-play
python scripts/125_sim_eval.py --games 245 --sims 150 --season 2024-25   # ~8 min: held-out scorecard
python scripts/export_web.py               # folds both into web/data.js
```

Run `156` after any trade, signing or draft. Rosters are NOT derived from game
data — doing that can only learn where a player last *played*, so across an
offseason it keeps traded players on their old team and cannot see a drafted
rookie at all.

**If you change the simulator and skip these, the Simulator tab keeps showing the
old engine while the rest of the site updates.** The scorecard is read from
`data/features/sim_eval_summary.json`, which script 125 writes, so the published
numbers always come from a real evaluation run rather than being typed in by hand.

## Put it online with Vercel (recommended, free)

**One-time setup:**
1. Make a free account at **github.com** and **vercel.com** (sign into Vercel *with* GitHub).
2. Push this project to a GitHub repo (from the project folder):
   ```
   git remote add origin https://github.com/<you>/nbai.git
   git push -u origin main
   ```
3. In Vercel: **Add New → Project → import your repo**.
   - **Root Directory:** set to `web`
   - **Framework Preset:** Other
   - Leave build/output empty (it's static). Click **Deploy**.
4. You get a live URL like `nbai.vercel.app` in ~30 seconds.

**Every future update:** run `python scripts/export_web.py`, then
stage only the intended web assets, commit, and push to `main` — Vercel redeploys automatically.

## Custom domain
In Vercel → your project → **Settings → Domains → Add**. It walks you through buying one
(or connecting one you own). `nbai.gg`, `nbai.app`, `nbai.io` are good candidates (~$10–15/yr).

## Keeping it fresh during the season (later)
A scheduled job (GitHub Actions) can run the scraper + `export_web.py` + push each morning,
so the site updates itself. We'll set that up once daily games are flowing.

## Season simulator

The production page is https://nbai-rho.vercel.app/season.html. Deploy all
`web/season*` files together; `season-data.js` is the generated schedule, roster
and model snapshot. `season-possession.js` is a separate possession-engine
snapshot so the game simulator and its published scorecard remain unchanged.
The payload records source checksums. Its inputs and offline exporter are
maintained in the research workspace; deploying the static page needs no build.

Run `node tests/test_season_engine.cjs` to check a full league season. For an
optional Playwright browser check, serve `web/` and set `NBAI_BASE_URL` to the
server URL before running `tests/check_season_browser.py`.

### Team hub

The season page now opens from one team's perspective. Its Monday–Sunday calendar
lets users browse the season and simulate the next league week. A team switch
updates roster, record, recent form, opponent and stat filters without resetting
results; roster edits still restart the preseason scenario. Ship
`season-calendar.js` with the other season assets. Run
`tests/check_team_hub_browser.py` for all 25 weekly boundaries, all 30 team records
and responsive layout checks. No simulation-model or schedule changes accompany
this interface update.


### Rebuilding season inputs

The season exporter and impact selection helper are now versioned in `scripts/`.
With the cached schedule PDF/receipt and research parquet inputs present, run
`python scripts/226_export_season_sim.py`, then deploy the updated season payload.
PyMuPDF is required to parse the official schedule. The payload records input
and model-source hashes; retain them when comparing results across revisions.
See `docs/research-notes/season-impact-fallback-2026-09-29.md` for the returning-player
correction and its full-league diagnostic.
