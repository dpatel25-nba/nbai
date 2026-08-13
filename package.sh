#!/usr/bin/env bash
# Bundle NBAI data + secrets for moving to another computer.
# The CODE is already on GitHub — this only packages what git does NOT track:
# the pulled data (incl. the PAID props) and your API key.
#
# Usage:  ./package.sh [output-dir]     (default: ~/Desktop)
set -euo pipefail
cd "$(dirname "$0")"

OUT="${1:-$HOME/Desktop}"
mkdir -p "$OUT"

echo "==> Bundling data/ (~7 GB raw; compresses to ~2-3 GB). This takes a few minutes..."
tar czf "$OUT/nbai-data.tgz" data/          # includes data/raw, parquet, features, .odds_key
echo "    $OUT/nbai-data.tgz  ($(du -h "$OUT/nbai-data.tgz" | cut -f1))"

# research findings / memory that live OUTSIDE the repo (also copied into docs/research-notes).
# Claude Code keys each project by its absolute path with "/" replaced by "-", so derive
# the slug from where this script actually lives instead of hardcoding one machine's path.
MEM="$HOME/.claude/projects/$(pwd -P | tr '/' '-')/memory"
if [ -d "$MEM" ] && [ -n "$(ls -A "$MEM" 2>/dev/null)" ]; then
  tar czf "$OUT/nbai-memory.tgz" -C "$(dirname "$MEM")" memory
  echo "    $OUT/nbai-memory.tgz  ($(du -h "$OUT/nbai-memory.tgz" | cut -f1))"
else
  echo "    ! no Claude Code memory found at $MEM — skipping nbai-memory.tgz"
  echo "      (the same findings are in docs/research-notes/, which travel with git)"
fi

# checksums so you can confirm the transfer wasn't corrupted
( cd "$OUT" && shasum -a 256 nbai-*.tgz > nbai-checksums.txt )
echo "    $OUT/nbai-checksums.txt"

echo ""
echo "==> DONE. Move the .tgz files to the new machine, then follow MIGRATION.md."
echo "    Verify integrity on the new machine with:  shasum -a 256 -c nbai-checksums.txt"
