---
name: modeling-architecture
description: Three-layer data design (raw → clean facts → leakage-safe features) the NBA pipeline builds toward for predictive modeling
metadata: 
  node_type: memory
  type: project
  originSessionId: 052674ad-d480-4e3c-940d-00c046841f89
---

The user's end goal is advanced stats + predictive modeling, so the pipeline is organized in three layers (agreed 2026-07-17):

1. **Raw JSON** (`data/raw/`): untouched API responses, per-game files. Insurance only, never modeled on.
2. **Clean fact tables** (`data/parquet/`): faithful to source, one grain per table, join keys (GAME_ID/PLAYER_ID/TEAM_ID) + real datetimes. Tables: game_logs (done), player_games (traditional+advanced+tracking merged), team_games, games (one row per game, home/away pivoted — the predictive workhorse), pbp, shots, players, teams.
3. **Model-ready features** (`data/features/`): built ONLY from Layer 2. This is where advanced/predictive stats live.

**Critical rule — point-in-time correctness / no leakage:** every feature for a game may use only info available before tip-off. All rolling stats are lagged (current game excluded). This is the #1 priority for the predictive work.

Planned Layer-3 features: per-100-possession normalization, rolling form (L5/L10/season, lagged), rest & schedule (days rest, back-to-backs, 3-in-4), opponent-adjusted ratings/SOS, home/away & H2H splits, rolling Elo, PBP-derived (lineup on/off, clutch, shot-quality xPTS). Advanced box stats (ORtg/DRtg/TS%/usage/PIE/pace/possessions) come straight from boxscoreadvancedv3.

Layers are rebuildable downward-only: re-run features without re-scraping. See [[nba-pipeline-status]] for scrape progress.
