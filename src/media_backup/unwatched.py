#!/usr/bin/env python3
"""
Generate a list of films in the media library that haven't been watched yet.
Uses fuzzy matching to handle title variations.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from media_backup.config import get_data_dir

# Try to use rapidfuzz for better performance, fall back to difflib
try:
    from rapidfuzz import fuzz as rapidfuzz

    USE_RAPIDFUZZ = True
except ImportError:
    USE_RAPIDFUZZ = False

# Fuzzy match threshold (0-100 for rapidfuzz, 0-1.0 for difflib)
MATCH_THRESHOLD = 85


def normalize_title(title: str) -> str:
    """Normalize title for comparison."""
    t = title.lower().strip()

    # Remove bracketed content like [Hausu], (2024), etc.
    t = re.sub(r"\[[^\]]*\]", "", t)
    t = re.sub(r"\([^)]*\)", "", t)

    # Remove common suffixes
    suffixes = [
        "directors cut",
        "director's cut",
        "remastered",
        "extended",
        "theatrical",
        "unrated",
        "special edition",
    ]
    for suffix in suffixes:
        t = t.replace(suffix, "")

    # Remove common prefixes for matching
    if t.startswith("the "):
        t = t[4:]

    # Normalize roman numerals to digits
    roman_map = [
        ("viii", "8"),
        ("vii", "7"),
        ("vi", "6"),
        ("iv", "4"),
        ("iii", "3"),
        ("ii", "2"),
        ("v", "5"),
        ("i", "1"),
    ]
    for roman, digit in roman_map:
        # Only replace if it's a standalone word
        t = re.sub(rf"\b{roman}\b", digit, t)

    # Remove punctuation that often varies
    for char in ".:'-,!?":
        t = t.replace(char, "")

    # Normalize whitespace
    t = " ".join(t.split())
    return t


def fuzzy_ratio(s1: str, s2: str) -> float:
    """Get fuzzy match ratio between two strings. Returns 0-100."""
    if USE_RAPIDFUZZ:
        return rapidfuzz.ratio(s1, s2)
    return SequenceMatcher(None, s1, s2).ratio() * 100


def token_sort_ratio(s1: str, s2: str) -> float:
    """Compare strings with tokens sorted (handles word order differences). Returns 0-100."""
    if USE_RAPIDFUZZ:
        return rapidfuzz.token_sort_ratio(s1, s2)
    tokens1 = sorted(s1.split())
    tokens2 = sorted(s2.split())
    return SequenceMatcher(None, " ".join(tokens1), " ".join(tokens2)).ratio() * 100


def is_watched(
    media_title: str, media_year: int | None, watched_list: list
) -> tuple[bool, str | None]:
    """
    Check if a film has been watched using fuzzy matching.
    Returns (is_watched, matched_title).
    """
    normalized_media = normalize_title(media_title)

    for watched in watched_list:
        watched_title = watched["title"]
        watched_year = watched.get("year")
        normalized_watched = normalize_title(watched_title)

        # First check: exact year match or within 1 year (release date variations)
        if media_year is None or watched_year is None:
            year_match = True
        else:
            year_match = abs(media_year - watched_year) <= 1

        if not year_match:
            continue

        # Direct sequence matching
        ratio = fuzzy_ratio(normalized_media, normalized_watched)

        if ratio >= MATCH_THRESHOLD:
            return True, watched_title

        # Also try token sort ratio for word order differences
        ts_ratio = token_sort_ratio(normalized_media, normalized_watched)
        if ts_ratio >= MATCH_THRESHOLD:
            return True, watched_title

    return False, None


def main() -> None:
    """Main entry point."""
    data_dir = get_data_dir()
    media_library_path = data_dir / "media_library.json"
    watched_path = data_dir / "films_already_watched.json"
    output_path = data_dir / "unwatched.txt"

    # Load media library
    with open(media_library_path) as f:
        media_library = json.load(f)

    # Load watched films
    with open(watched_path) as f:
        watched_list = json.load(f)

    # Filter to movies only and find unwatched
    unwatched = []
    for item in media_library:
        if item.get("type") != "movie":
            continue

        # Skip items with errors or missing titles
        if item.get("error") or not item.get("title"):
            continue

        title = item["title"]
        year = item.get("year")

        watched, _matched = is_watched(title, year, watched_list)
        if not watched:
            unwatched.append((year, title))

    # Sort by year, then title (None years sort first)
    unwatched.sort(key=lambda x: (x[0] or 0, x[1].lower()))

    # Generate output
    lines = ["# Unwatched Films in Media Library", ""]
    lines.append(f"Total: {len(unwatched)} films\n")

    for year, title in unwatched:
        year_str = str(year) if year else "????"
        lines.append(f"({year_str}) {title}")

    output = "\n".join(lines) + "\n"

    # Write to file
    with open(output_path, "w") as f:
        f.write(output)

    # Also print to stdout
    print(output)
    print(f"Written to {output_path}")


if __name__ == "__main__":
    main()
