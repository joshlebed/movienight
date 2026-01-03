#!/bin/bash
# Daily backup script for media library data
#
# This script:
# 1. Scrapes Letterboxd watched films
# 2. Scans local media directories
# 3. Computes unwatched films
# 4. Commits and pushes changes to the data repo

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$REPO_DIR/data"

cd "$REPO_DIR"

echo "=== Media Library Backup - $(date) ==="

# Ensure data directory exists
if [ ! -d "$DATA_DIR" ]; then
    echo "Error: data/ directory not found. Run setup first."
    exit 1
fi

# Step 1: Scrape Letterboxd watched films
echo ""
echo "--- Scraping Letterboxd watched films ---"
uv run letterboxd

# Step 2: Scan local media directories
echo ""
echo "--- Scanning local media library ---"
uv run snapshot

# Step 3: Generate unwatched list
echo ""
echo "--- Generating unwatched list ---"
uv run unwatched

# Step 4: Commit and push data repo
echo ""
echo "--- Committing to data repo ---"
cd "$DATA_DIR"

# Files to track in data repo
TRACKED_FILES="media_library.json media_list.txt films_already_watched.json unwatched.txt"

# Check if data dir is a git repo
if [ ! -d ".git" ]; then
    echo "Warning: data/ is not a git repo. Skipping commit."
    exit 0
fi

# Check if there are changes to commit
has_changes=false
for file in $TRACKED_FILES; do
    if [ -f "$file" ]; then
        if ! git diff --quiet "$file" 2>/dev/null || ! git diff --cached --quiet "$file" 2>/dev/null; then
            has_changes=true
            break
        fi
        if ! git ls-files --error-unmatch "$file" &>/dev/null 2>&1; then
            has_changes=true
            break
        fi
    fi
done

if $has_changes; then
    git add $TRACKED_FILES

    if ! git ls-files --error-unmatch media_library.json &>/dev/null 2>&1; then
        git commit -m "Initial media library backup"
    else
        git commit -m "Update media library - $(date '+%Y-%m-%d')"
    fi

    if git remote | grep -q .; then
        git push
        echo "Changes pushed to remote"
    else
        echo "No remote configured, skipping push"
    fi
else
    echo "No changes to commit"
fi

echo ""
echo "=== Backup complete ==="
