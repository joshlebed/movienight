#!/bin/bash
# Daily backup script for media library data
#
# This script:
# 1. Scrapes Letterboxd watched films and watchlists for all users
# 2. Scans local media directories
# 3. Generates per-user filtered lists
# 4. Commits and pushes changes to the data repo

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$REPO_DIR/data"

cd "$REPO_DIR"

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

# Add all trackable files
git add cache/*.json reports/*.md 2>/dev/null || true

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

echo ""
echo "=== Backup complete ==="
