#!/usr/bin/env python3
"""
Generate per-user lists comparing local media library against Letterboxd data.

For each user, generates:
- {user}_watchlist_available.txt: Local films on user's watchlist (ready to watch)
- {user}_undiscovered.txt: Local films not watched and not on watchlist
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from media_backup.config import get_data_dir, load_config

try:
    from rapidfuzz import fuzz as rapidfuzz
    USE_RAPIDFUZZ = True
except ImportError:
    USE_RAPIDFUZZ = False

MATCH_THRESHOLD = 85


def normalize_title(title: str) -> str:
    """Normalize title for comparison."""
    t = title.lower().strip()

    # Remove bracketed content
    t = re.sub(r"\[[^\]]*\]", "", t)
    t = re.sub(r"\([^)]*\)", "", t)

    # Remove common suffixes
    for suffix in ["directors cut", "director's cut", "remastered", "extended",
                   "theatrical", "unrated", "special edition"]:
        t = t.replace(suffix, "")

    # Remove "the" prefix
    if t.startswith("the "):
        t = t[4:]

    # Normalize roman numerals
    for roman, digit in [("viii", "8"), ("vii", "7"), ("vi", "6"), ("iv", "4"),
                         ("iii", "3"), ("ii", "2"), ("v", "5"), ("i", "1")]:
        t = re.sub(rf"\b{roman}\b", digit, t)

    # Remove punctuation
    for char in ".:'-,!?":
        t = t.replace(char, "")

    return " ".join(t.split())


def fuzzy_ratio(s1: str, s2: str) -> float:
    """Get fuzzy match ratio (0-100)."""
    if USE_RAPIDFUZZ:
        return rapidfuzz.ratio(s1, s2)
    return SequenceMatcher(None, s1, s2).ratio() * 100


def token_sort_ratio(s1: str, s2: str) -> float:
    """Compare with tokens sorted (0-100)."""
    if USE_RAPIDFUZZ:
        return rapidfuzz.token_sort_ratio(s1, s2)
    tokens1 = sorted(s1.split())
    tokens2 = sorted(s2.split())
    return SequenceMatcher(None, " ".join(tokens1), " ".join(tokens2)).ratio() * 100


def find_match(title: str, year: int | None, film_list: list[dict]) -> dict | None:
    """Find a matching film in a list using fuzzy matching."""
    normalized = normalize_title(title)

    for film in film_list:
        film_title = film["title"]
        film_year = film.get("year")
        normalized_film = normalize_title(film_title)

        # Year check (allow 1 year variance)
        if year is not None and film_year is not None:
            if abs(year - film_year) > 1:
                continue

        # Fuzzy matching
        if fuzzy_ratio(normalized, normalized_film) >= MATCH_THRESHOLD:
            return film
        if token_sort_ratio(normalized, normalized_film) >= MATCH_THRESHOLD:
            return film

    return None


def load_json(path: Path) -> list[dict]:
    """Load JSON file, returning empty list if not found."""
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def get_local_movies(data_dir: Path) -> list[dict]:
    """Get movies from local media library."""
    media_path = data_dir / "media_library.json"
    media = load_json(media_path)

    return [
        item for item in media
        if item.get("type") == "movie"
        and not item.get("error")
        and item.get("title")
    ]


def format_film_list(films: list[tuple[int | None, str]], header: str) -> str:
    """Format a list of films for output."""
    lines = [header, "", f"Total: {len(films)} films", ""]
    for year, title in sorted(films, key=lambda x: (x[0] or 0, x[1].lower())):
        year_str = str(year) if year else "????"
        lines.append(f"({year_str}) {title}")
    return "\n".join(lines) + "\n"


def process_user(username: str, local_movies: list[dict], data_dir: Path) -> None:
    """Generate filtered lists for a single user."""
    print(f"Processing user: {username}", file=sys.stderr)

    watched_path = data_dir / f"{username}_watched.json"
    watchlist_path = data_dir / f"{username}_watchlist.json"

    watched = load_json(watched_path)
    watchlist = load_json(watchlist_path)

    if not watched and not watchlist:
        print(f"  No data found for {username}, skipping", file=sys.stderr)
        return

    print(f"  Watched: {len(watched)}, Watchlist: {len(watchlist)}", file=sys.stderr)

    watchlist_available = []  # Local films on watchlist
    undiscovered = []  # Local films not watched and not on watchlist

    for movie in local_movies:
        title = movie["title"]
        year = movie.get("year")

        is_watched = find_match(title, year, watched) is not None
        is_on_watchlist = find_match(title, year, watchlist) is not None

        if is_on_watchlist and not is_watched:
            watchlist_available.append((year, title))
        elif not is_watched and not is_on_watchlist:
            undiscovered.append((year, title))

    # Write watchlist available
    watchlist_out = data_dir / f"{username}_watchlist_available.txt"
    with open(watchlist_out, "w") as f:
        f.write(format_film_list(
            watchlist_available,
            f"# {username}'s Watchlist - Available Locally"
        ))
    print(f"  Watchlist available: {len(watchlist_available)} films", file=sys.stderr)

    # Write undiscovered
    undiscovered_out = data_dir / f"{username}_undiscovered.txt"
    with open(undiscovered_out, "w") as f:
        f.write(format_film_list(
            undiscovered,
            f"# {username}'s Undiscovered Films (not watched, not on watchlist)"
        ))
    print(f"  Undiscovered: {len(undiscovered)} films", file=sys.stderr)


def main() -> None:
    config = load_config()
    data_dir = get_data_dir()

    # Get users from config
    users = config.get("letterboxd_users", [])
    if not users and config.get("letterboxd_username"):
        users = [config["letterboxd_username"]]

    ap = argparse.ArgumentParser(description="Generate per-user film lists")
    ap.add_argument(
        "--users",
        nargs="+",
        default=users,
        help="Letterboxd usernames (default: from config.json)",
    )
    args = ap.parse_args()

    if not args.users:
        print("Error: No users specified")
        raise SystemExit(1)

    local_movies = get_local_movies(data_dir)
    print(f"Local library: {len(local_movies)} movies", file=sys.stderr)

    for username in args.users:
        process_user(username, local_movies, data_dir)

    print("Done", file=sys.stderr)


if __name__ == "__main__":
    main()
