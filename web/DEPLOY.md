# Deploying NBAI (first-timer guide)

## Current product pages

- `index.html`: home page, product introduction and saved research highlights.
- `simulators.html`: hub for the single-game and full-season experiences.
- `game.html`: game simulator and retained detailed research views. Old
  `index.html#players`-style links redirect here.
- `season.html`: team-perspective season simulator.
- `insights.html`: player search, season points/assists/rebounds scenarios, manual
  season-line comparisons and available Kalshi player props.
- `player-value.html`: retained historical player-value archive.
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

The pages are plain files in `web/`, with one read-only Vercel Function for live quotes.
Saved quote snapshots remain available without that function. `data.js` is generated from the model by `scripts/export_web.py`.

## Preview it locally
Serve the complete folder plus the read-only quote endpoint:

```sh
agent-workspace/.runtime/node/bin/node scripts/serve_website.cjs
```

Open `http://127.0.0.1:8770/`. A plain static server also works with saved quotes,
but cannot serve the live API. Shared navigation is ordinary HTML; after changing
its structure in `scripts/build_site_navigation.py`, regenerate all seven product
headers with `python scripts/build_site_navigation.py`. New home/hub styles are
in `home.css`; shared navigation styles and behavior are in `site.css` / `site.js`.

Check the home page, old research links and both simulator flows:

```sh
PLAYWRIGHT_BROWSERS_PATH=agent-workspace/.runtime/browsers \
  agent-workspace/.venv/bin/python tests/check_home_browser.py
```

### Player props and live quotes

Vercel's project root remains `web`. Deploy `api/kalshi.js`, `lib/kalshi.cjs`
and `vercel.json` with the pages. The function permits only public GET market
inventory requests for fixed player-stat or team-season series. It uses no
account keys, balances, orders or trade endpoints. Inventories are cursor-complete
or return an error; an incomplete result never replaces a saved snapshot.

The function has a 20-second retrieval deadline, 30-second cache, request
coalescing per warm instance and a 30-second function limit. Visible browser
pages refresh every 60 seconds. Observation times and response SHA-256 hashes
travel with quotes. Failure retains the saved timestamp and labels quotes as
saved; stale or closed player quotes cannot produce a directional lean. This is
polled market data, not a streaming execution feed.

Generate a new reproducible projection snapshot with:

```sh
agent-workspace/.runtime/node/bin/node scripts/build_player_projections.cjs
agent-workspace/.runtime/node/bin/node --test tests/test_player_props.cjs
PLAYWRIGHT_BROWSERS_PATH=agent-workspace/.runtime/browsers \
  agent-workspace/.venv/bin/python tests/check_player_props_browser.py
```

The exporter runs 200 seasons across all 30 teams. It records input hashes,
protocol and samples under `data/features/player_props/runs/`. Per-game and
total units are separate; no minutes projects as unavailable. These are
fixed-roster scenarios with sampled historical injury/illness absences, not
calibrated betting probabilities. Run `scripts/build_season_availability.py`
first to rebuild the five-season health prior and update the saved season input.
The player exporter also creates `season-win-projections.json`: 200 injury runs
paired with 200 healthy diagnostic runs using the same game seeds.

Health profiles use explicit injury/illness, concussion, reconditioning and
health/safety non-participation comments in 2021–22 through 2025–26 game records.
Coach DNPs, generic missing rows and rest are not diagnosed as injuries. This
incomplete observation population can understate true missed-game risk; rates
must not be presented as medically validated forecasts. Player spells are
independent. The season engine rebuilds rotations, recalculates available-player
impact and uses roster-sensitive calibration keys; excluded players receive no
box-score minutes. Insufficient healthy players cause an explicit error instead
of silently bringing an injured player back. Explicit `absenceWindows` can be
supplied to the engine for a dated scenario; no stale current injury file is read.

The healthy setting is diagnostic only. Previous all-healthy shortlist leans
are withdrawn. Historical team ratings already include some availability
effects; combined injury adjustments and replacement rotations need validation
before recommending win-total bets. Known current injuries and recovery windows
remain a separate integration requirement.

October 5 discovery found game points contracts but no season over/under
contracts in the supported points/assists/rebounds series. The page distinguishes
empty inventories, retrieval failures and unmatched players. A manual line is
user supplied, not a sourced bookmaker quote. Season projections never supply
game-prop recommendations. MVP collection and shortlist research are removed.

Primary API reference: [Kalshi public market data](https://docs.kalshi.com/getting_started/quick_start_market_data).

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

## Daily game board

`betting.html` now hosts Game day, replacing the season-line picks page. Ship
`betting.js`, `day-board.js`, `day-board.css`, `consensus-analysis.js`, and the shared
site assets with it. Dates use America/New_York; game links retain the date and
market event ID. Game and season simulators remain under Simulators.

The existing six-hour `refresh-player-lines.yml` workflow now requests both player
props and main moneyline/spread/total markets. It retains the approved 300-credit
maximum per scan. To refresh locally:

```sh
python scripts/collect_player_consensus.py --collect --max-events 60 --max-credits 300 --game-markets
node scripts/build_market_game_projections.cjs
node tests/test_market_game_export.cjs
node scripts/build_player_line_coverage.cjs
```

Publish `player-consensus.json`, `player-game-projections.json`,
`player-line-coverage.json`, and `player-consensus-status.json` together. The cloud
workflow retains the previous verified data bundle if collection or verification
fails. Source receipts are retained as Actions artifacts for seven days. Do not
publish local credentials or raw unredacted requests.

Game scores and player projections come from the same 200 possession-based
scenarios per modeled matchup. Scheduled games without a verified model remain
visible with a pending forecast. Rotation review holds suppress forecasts and
shortlists. Bookmaker prices are timestamped snapshots; they are not live scores
or streaming odds. Quotes older than 15 minutes are labeled saved. The featured props can retain
saved comparisons, while current leans require recent matching prices. Parlay
drafts require two or three distinct player/stat selections at the same sportsbook
and verify the model direction against each actual book line. They do not claim
combined odds, joint win probability, or verified same-game parlay acceptance.
The player browser includes unquoted rostered players and all eight projected stats.
Current medical reports, confirmed lineups, calibrated price-based betting
validation, and live game feeds remain launch dependencies. No trade execution is
connected to this page.

Checks: `node --test tests/test_day_board.cjs`,
`python tests/test_game_market_quotes.py`, and
`python tests/check_day_board_browser.py` (requires Playwright; set
`NBAI_BASE_URL` for a deployed site). Use an explicit release file list when
publishing from this workspace, since it contains unrelated ongoing work.

## Visual research gallery

`insights.html` is now the historical research gallery. Season projections and
player-market comparisons are no longer loaded by this page; matchup comparisons
remain in Game day. Ship `visual-research.js`, `visual-research.css`,
`visual-research.json`, `research-studies.js`, and `research-tooltips.js` together. The homepage uses the
same historical dataset.

Rebuild the three-season snapshot with `python scripts/build_visual_research.py`
(pandas/numpy/parquet support). It exports 2023–24 through 2025–26 regular seasons:
all 30 teams, binned recorded shot locations, and players with at least 500 minutes.
WAR v4 components are retained from the archived research model; they are not
refitted or represented as causal, independently validated impact. Usage and true
shooting are historical rates. Missing coordinates and beyond-half-court attempts
have explicit coverage counts. Input and formula hashes are in the JSON.

Run `python scripts/export_research_graphics.py` with Matplotlib to regenerate
the archived 2025–26 PNG editions. The shareable-edition and notebook promotion
sections have been removed from Research. SVG downloads on the two original
interactive charts still capture the current selection.

Validation: `python tests/test_visual_research.py` checks shot count reconciliation,
coordinate exclusions and archived player metrics. `python tests/check_visual_research_browser.py`
checks four viewport widths, player/team/season filters, exports and load failures.
Set `NBAI_BASE_URL=https://nbai.space` for deployed browser verification.

The gallery now has five visible charts. The additional studies are scoring
surplus per 36 minutes, assists versus turnovers, and team shot-mix baseline
versus finishing surplus. All three follow the global season selector, with visible
searchable/sortable tables. Definitions live in `metricDefinitions` in the data
export. Scoring surplus uses full-league TS at the player’s shooting volume; team
shot value decomposes actual points per 100 FGA into a league-zone-rate baseline
and a finishing residual. They are descriptive measures, not causal impact or
validated predictions. Tests cover signed baseline examples, rate normalization,
all-team weighted residual reconciliation, three seasons and UI filtering.

The shared tooltip controller provides hover, tap, keyboard focus and Escape
dismissal on impact/efficiency dots, scoring-surplus bars, assists/turnovers dots,
team shot-value dots and court bins. Details include identity, named metrics and
season; filters rebuild them with the current data. Player selection and SVG
downloads remain available. Load `research-tooltips.js` before both chart scripts.

## Trading beta: historical sandbox

`trading.html` is the first visitor-facing algorithm sandbox. Ship it together with
`trading.css`, `trading.js`, `trading-engine.js`, `trading-replay.json` and the shared
navigation assets. `python scripts/build_site_navigation.py` links it from the
eight main product pages without changing their bodies.

Rebuild the allowlisted archive with `python scripts/export_trading_sandbox.py`.
It uses the existing 20261004-v1 Kalshi report: 813 January–June 2026 games, all
30 teams, including the excluded observation. It exports no account data or local
filesystem paths. The source report hash identifies the exact replay version.

Visitors can replay days automatically or individually, pause, set virtual
bankroll/daily-spend/cumulative-loss limits, inspect every decision and download
the ledger. Only a source hash, settings and replay position persist in browser
storage; reload reconstructs the ledger and always pauses. Hidden tabs pause too.
No account connection, server scheduling, billing, orders or RFQs exist here.
Combos and price timing are labeled unavailable research tracks.

This is an educational historical scenario, not live paper execution or fresh
validation. One hypothetical contract per selected game uses saved fee/cost
scenarios and actual settlement values. Daily batch accounting reserves costs
before crediting outcomes; actual settlement timing, depth and fills are not
verified. Loss pauses take effect at day end, so the day's losses can overshoot.
The original model's negative result is visible, and settings experiments must
not be promoted as evidence of profitability.

Run `node --test tests/test_trading_sandbox.cjs`. The full unrestricted replay
must match the archived 405 hypothetical contracts, $162.87 cost and −$19.87 net
under the 1¢ adverse-price scenario. Check the page at mobile and desktop widths,
including reload, pause, settings validation, downloads and archive-load failure.
Local preview: `node scripts/serve_website.cjs`, then `/trading.html`.
