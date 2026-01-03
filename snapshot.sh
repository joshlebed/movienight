#!/bin/bash
# Snapshot script for movies and TV shows
# Generates a human-readable list sorted alphabetically (one per line for clean git diffs)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_FILE="$SCRIPT_DIR/media_list.txt"

MOVIES_DIR="/mnt/vault/movies"
TV_DIR="/mnt/vault/tv"

{
    echo "# Media Library Snapshot"
    echo ""
    echo "## Movies"
    echo ""

    if [[ -d "$MOVIES_DIR" ]]; then
        for dir in "$MOVIES_DIR"/*/; do
            [[ -d "$dir" ]] || continue
            basename "$dir"
        done | sort
    else
        echo "(directory not found: $MOVIES_DIR)"
    fi

    echo ""
    echo "## TV Shows"
    echo ""

    if [[ -d "$TV_DIR" ]]; then
        for dir in "$TV_DIR"/*/; do
            [[ -d "$dir" ]] || continue
            basename "$dir"
        done | sort
    else
        echo "(directory not found: $TV_DIR)"
    fi
} > "$OUTPUT_FILE"

echo "Snapshot saved to $OUTPUT_FILE"
