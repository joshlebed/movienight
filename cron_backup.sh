#!/bin/bash
# Cron job script to snapshot media library and commit changes to git
# Run daily via cron

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# Run the snapshot script
./snapshot.sh

# Function to commit and push
commit_and_push() {
    local msg="$1"
    git add media_list.txt
    git commit -m "$msg"

    # Only push if remote is configured
    if git remote | grep -q .; then
        git push
        echo "$msg - pushed to remote"
    else
        echo "$msg - no remote configured, skipping push"
    fi
}

# Check if there are changes to commit
if git diff --quiet media_list.txt 2>/dev/null && git diff --cached --quiet media_list.txt 2>/dev/null; then
    # No changes, check if file is untracked
    if ! git ls-files --error-unmatch media_list.txt &>/dev/null; then
        commit_and_push "Initial media library snapshot"
    else
        echo "No changes to media library"
    fi
else
    commit_and_push "Update media library snapshot - $(date '+%Y-%m-%d')"
fi
