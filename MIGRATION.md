# Moving NBAI to another computer

The **code** is on GitHub (this repo). Only the **data** and your **API key** aren't
tracked by git (they're large / secret), so those are the only things to move by hand.
**You never have to re-pull or re-pay for anything** — copying the files is the transfer.

---

## On the OLD machine (the current one)

1. Build the bundle:
   ```bash
   cd ~/Documents/nba-data
   ./package.sh ~/Desktop
   ```
   This creates on your Desktop:
   - `nbai-data.tgz` — all data: raw JSON, parquet tables, features, **the paid props**, and your `.odds_key` (~2–3 GB compressed)
   - `nbai-memory.tgz` — the research-findings/context (tiny)
   - `nbai-checksums.txt` — to verify the transfer

2. Move those 3 files to the new machine — any of: external drive, Google Drive/Dropbox,
   AirDrop, or over the network (`scp nbai-*.tgz you@newmachine:~/`).

---

## On the NEW machine

**Prereqs:** Python 3.9+ and git installed. (Windows: use WSL or Git Bash.)

1. Clone the code:
   ```bash
   git clone https://github.com/dpatel25-nba/nbai.git ~/Documents/nba-data
   cd ~/Documents/nba-data
   ```

2. Recreate the Python environment (do NOT copy the old `venv/` — it's platform-specific):
   ```bash
   python3 -m venv venv
   ./venv/bin/pip install -r requirements.txt
   ```

3. Restore the data (put `nbai-data.tgz` in the project root first):
   ```bash
   shasum -a 256 -c nbai-checksums.txt      # optional: confirm the transfer is intact
   tar xzf nbai-data.tgz                     # recreates data/ with everything incl. paid props + key
   ```

4. Verify it works:
   ```bash
   ./venv/bin/python scripts/116_props_clv.py
   ```
   If it prints the opening-line CLV results, **you're fully migrated.**

---

## Restoring the research context (optional)

All findings are also saved in `docs/research-notes/` (they travel with the git clone),
so the history is portable either way. To restore them as live Claude Code *memory*:
- Extract `nbai-memory.tgz` into
  `~/.claude/projects/<encoded-project-path>/memory/` on the new machine.
- The `<encoded-project-path>` is the project's full path with `/` replaced by `-`
  (e.g. `/Users/alex/Documents/nba-data` → `-Users-alex-Documents-nba-data`).
- If unsure, skip it — the notes in `docs/research-notes/MEMORY.md` cover everything.

---

## Notes
- **Nothing is re-pulled.** The paid props (`data/raw/props_odds/`) are inside the bundle.
- The 3.8 GB of play-by-play JSON is the biggest chunk; it's only needed to *rebuild*
  parquet tables. If you want a smaller bundle and don't plan to rebuild, you can skip it:
  `tar czf ~/Desktop/nbai-lean.tgz data/parquet data/features data/raw/props_odds data/raw/odds data/.odds_key`
- Your API key is in `data/.odds_key` (gitignored, included in the data bundle — never on GitHub).
