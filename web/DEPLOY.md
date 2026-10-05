# Deploying NBAI (first-timer guide)

## Current product pages

- `index.html`: home page, product introduction and saved research highlights.
- `simulators.html`: hub for the single-game and full-season experiences.
- `game.html`: game simulator and retained detailed research views. Old
  `index.html#players`-style links redirect here.
- `season.html`: team-perspective season simulator.
- `insights.html`: original statistics, player comparisons and formula explanations.
- `betting.html`: captured preseason Kalshi markets and a conditional research watchlist.
- `research.html`, `kalshi-history.html`, `kalshi-lab.html`: supporting evidence.

After refreshing `web/data.js`, regenerate the compact original-stat snapshot:

```sh
agent-workspace/.runtime/node/bin/node scripts/build_insights.cjs
```

Refresh public season quotes and check them against their retained HTTP receipts:

```sh
agent-workspace/.venv/bin/python scripts/build_season_betting.py --refresh
agent-workspace/.venv/bin/python scripts/audit_season_betting.py
```

The market collector uses public reads only. It does not place trades or update
forecasts. Prices are snapshots; observation times are not exchange price-change
times. Keep the JSON files alongside the HTML/JS/CSS when deploying `web/`.

Run responsive product checks (including missing/stale snapshots) locally:

```sh
PLAYWRIGHT_BROWSERS_PATH=agent-workspace/.runtime/browsers \
  agent-workspace/.venv/bin/python tests/check_product_browser.py
```

These steps prepare local files. Publishing still uses the existing GitHub/Vercel
workflow; passing local checks does not mean a deployment happened.

The site is a **static site**: plain files in `web/` (`index.html` + `data.js`). No server
needed. `data.js` is generated from the model by `scripts/export_web.py`.

## Preview it locally
Serve the complete folder so pages can load their JSON snapshots:

```sh
python -m http.server 8769 --bind 127.0.0.1 --directory web
```

Open `http://127.0.0.1:8769/`. Shared navigation is ordinary HTML; after changing
its structure in `scripts/build_site_navigation.py`, regenerate all six product
headers with `python scripts/build_site_navigation.py`. New home/hub styles are
in `home.css`; shared navigation styles and behavior are in `site.css` / `site.js`.

Check the home page, old research links and both simulator flows:

```sh
PLAYWRIGHT_BROWSERS_PATH=agent-workspace/.runtime/browsers \
  agent-workspace/.venv/bin/python tests/check_home_browser.py
```

To refresh the numbers after the model changes:

```
python scripts/export_web.py     # regenerates web/data.js
```

### Refreshing the Simulator tab

`export_web.py` only *reads* the simulator payload; it does not regenerate it,
because doing so runs the possession engine 30 times. Two files feed that tab and
both are snapshots of whatever the engine was when they were written:

```
python scripts/156_pull_rosters.py --season 2026-27   # who is actually on each team
python scripts/170_injuries.py             # refresh availability and archive today's snapshot
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

The evaluation uses projected minutes but knows who actually played. Its scorecard
is conditional on that availability information, not a validated live pregame result.
Shared fitted simulator components also lack enforced historical training cutoffs;
the scorecard is retrospective. The isolated `183`/`184` research pipeline avoids
those components and fits each model only on earlier seasons.
Run `170_injuries.py` daily to build availability history, then
`171_status_playrate.py` to check whether enough subsequent games exist to estimate
play rates. These commands do not install a daily schedule.

## Put it online with Vercel (recommended, free)

### Season simulator

`season.html` is linked from the main navigation. Ship `season.html`,
`season.css`, `season-ui.js`, `season-engine.js`, `season-data.js`, and `sim.js`
together. Rebuild its separate data payload with
`python scripts/226_export_season_sim.py`; use `--download-schedule` to refresh
the official release PDF. The existing `export_web.py` does not overwrite this
payload. See [season simulator documentation](../docs/SEASON_SIMULATOR.md).

### Publishing the static site

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
`git add -A && git commit -m "update data" && git push` — Vercel redeploys automatically.

## Custom domain
In Vercel → your project → **Settings → Domains → Add**. It walks you through buying one
(or connecting one you own). `nbai.gg`, `nbai.app`, `nbai.io` are good candidates (~$10–15/yr).

## Keeping it fresh during the season (later)
A scheduled job (GitHub Actions) can run the scraper + `export_web.py` + push each morning,
so the site updates itself. We'll set that up once daily games are flowing.
