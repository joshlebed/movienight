#!/bin/bash
# Daily backup script for media library data
#
# This script:
# 1. Scrapes Letterboxd watched films and watchlists for all users
# 2. Scans local media directories
# 3. Generates per-user filtered lists
# 4. Commits and pushes changes to the data repo
# 5. Posts a Discord digest of per-user watchlist additions since the
#    previous run (best-effort)

set -e

# Add common bin directories to PATH for cron environment
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$REPO_DIR/data"

cd "$REPO_DIR"

# Serialize against the */5 hook-driven refresh in cron_refresh_on_flag.sh —
# both write data/cache/media_library.json and data/reports/*.md, and
# concurrent runs would interleave. We wait (the daily run is more important
# than skipping a poll); the poller does -n and skips if we hold the lock.
exec 9>/tmp/movienight-refresh.lock
flock -w 600 9 || { echo "ERROR: failed to acquire /tmp/movienight-refresh.lock after 600s"; exit 1; }

echo "=== Media Library Backup - $(date) ==="

if [ ! -d "$DATA_DIR" ]; then
    echo "Error: data/ directory not found. Run setup first."
    exit 1
fi

# Step 1: Scrape Letterboxd with ratings
echo ""
echo "--- Scraping Letterboxd ---"
uv run letterboxd --ratings

# Step 2: Scan local media
echo ""
echo "--- Scanning local media ---"
uv run snapshot

# Step 3: Generate per-user lists
echo ""
echo "--- Generating filtered lists ---"
uv run unwatched

# Step 4: Commit and push
echo ""
echo "--- Committing to data repo ---"
cd "$DATA_DIR"

if [ ! -d ".git" ]; then
    echo "Warning: data/ is not a git repo. Skipping commit."
    exit 0
fi

git add . || true

if git diff --cached --quiet; then
    echo "No changes to commit"
else
    git commit -m "Update media library - $(date '+%Y-%m-%d')"

    if git remote | grep -q .; then
        git push
        echo "Changes pushed to remote"
    else
        echo "No remote configured, skipping push"
    fi
fi

# Step 5: Post Discord digest of watchlist additions since the last run.
# Compares HEAD~1 (yesterday's commit, post-step-4) to HEAD and posts a
# single embed listing per-user additions. Reads DISCORD_WEBHOOK_URL from
# media-stack/.env. Best-effort — failure here doesn't fail the cron.
echo ""
echo "--- Posting Discord digest ---"
PREV_COMMIT=$(git log --skip=1 -n 1 --format='%H' 2>/dev/null || true)
if [ -n "$PREV_COMMIT" ]; then
    "$REPO_DIR/scripts/post_discord_digest.py" "$PREV_COMMIT" "HEAD" || \
        echo "(digest post failed; continuing)"
else
    echo "(no previous commit yet — skipping first-run digest)"
fi

echo ""
echo "=== Backup complete ==="
