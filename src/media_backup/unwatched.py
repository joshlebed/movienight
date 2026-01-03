#!/usr/bin/env python3
"""
Generate per-user and pairwise lists comparing local media against Letterboxd data.

Per-user outputs:
- {user}_watchlist_available.txt: Watchlist films available locally
- {user}_watchlist_missing.txt: Watchlist films NOT available locally
- {user}_undiscovered.txt: Local films not watched, not on watchlist

Pairwise outputs (for each pair of users):
- {user1}_{user2}_shared_watchlist_available.txt: Shared watchlist, available locally
- {user1}_{user2}_shared_watchlist_missing.txt: Shared watchlist, NOT available locally
"""

from __future__ import annotations

import argparse
import itertools
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
    t = re.sub(r"\[[^\]]*\]", "", t)
    t = re.sub(r"\([^)]*\)", "", t)

    for suffix in ["directors cut", "director's cut", "remastered", "extended",
                   "theatrical", "unrated", "special edition"]:
        t = t.replace(suffix, "")

    if t.startswith("the "):
        t = t[4:]

    for roman, digit in [("viii", "8"), ("vii", "7"), ("vi", "6"), ("iv", "4"),
                         ("iii", "3"), ("ii", "2"), ("v", "5"), ("i", "1")]:
        t = re.sub(rf"\b{roman}\b", digit, t)

    for char in ".:'-,!?":
        t = t.replace(char, "")

    return " ".join(t.split())


def fuzzy_ratio(s1: str, s2: str) -> float:
    if USE_RAPIDFUZZ:
        return rapidfuzz.ratio(s1, s2)
    return SequenceMatcher(None, s1, s2).ratio() * 100


def token_sort_ratio(s1: str, s2: str) -> float:
    if USE_RAPIDFUZZ:
        return rapidfuzz.token_sort_ratio(s1, s2)
    tokens1 = sorted(s1.split())
    tokens2 = sorted(s2.split())
    return SequenceMatcher(None, " ".join(tokens1), " ".join(tokens2)).ratio() * 100


def films_match(film1: dict, film2: dict) -> bool:
    """Check if two films match using fuzzy matching."""
    n1 = normalize_title(film1["title"])
    n2 = normalize_title(film2["title"])

    y1 = film1.get("year")
    y2 = film2.get("year")
    if y1 is not None and y2 is not None and abs(y1 - y2) > 1:
        return False

    return fuzzy_ratio(n1, n2) >= MATCH_THRESHOLD or token_sort_ratio(n1, n2) >= MATCH_THRESHOLD


def find_in_list(film: dict, film_list: list[dict]) -> bool:
    """Check if film exists in list."""
    for f in film_list:
        if films_match(film, f):
            return True
    return False


def find_local_match(film: dict, local_movies: list[dict]) -> dict | None:
    """Find matching local movie for a film."""
    for movie in local_movies:
        if films_match({"title": movie["title"], "year": movie.get("year")}, film):
            return movie
    return None


def load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def get_local_movies(data_dir: Path) -> list[dict]:
    media_path = data_dir / "media_library.json"
    media = load_json(media_path)
    return [
        item for item in media
        if item.get("type") == "movie" and not item.get("error") and item.get("title")
    ]


def format_film_list(films: list[tuple[int | None, str]], header: str) -> str:
    lines = [header, "", f"Total: {len(films)} films", ""]
    for year, title in sorted(films, key=lambda x: (x[0] or 0, x[1].lower())):
        year_str = str(year) if year else "????"
        lines.append(f"({year_str}) {title}")
    return "\n".join(lines) + "\n"


def process_user(username: str, local_movies: list[dict], data_dir: Path) -> dict:
    """Generate filtered lists for a single user. Returns user data for pairwise processing."""
    print(f"Processing user: {username}", file=sys.stderr)

    watched = load_json(data_dir / f"{username}_watched.json")
    watchlist = load_json(data_dir / f"{username}_watchlist.json")

    if not watched and not watchlist:
        print(f"  No data found for {username}, skipping", file=sys.stderr)
        return {"watched": [], "watchlist": []}

    print(f"  Watched: {len(watched)}, Watchlist: {len(watchlist)}", file=sys.stderr)

    watchlist_available = []
    watchlist_missing = []
    undiscovered = []

    # Process watchlist
    for film in watchlist:
        is_watched = find_in_list(film, watched)
        if is_watched:
            continue  # Skip films already watched

        local_match = find_local_match(film, local_movies)
        if local_match:
            watchlist_available.append((film.get("year"), film["title"]))
        else:
            watchlist_missing.append((film.get("year"), film["title"]))

    # Process local movies for undiscovered
    for movie in local_movies:
        title = movie["title"]
        year = movie.get("year")
        movie_dict = {"title": title, "year": year}

        is_watched = find_in_list(movie_dict, watched)
        is_on_watchlist = find_in_list(movie_dict, watchlist)

        if not is_watched and not is_on_watchlist:
            undiscovered.append((year, title))

    # Write outputs
    with open(data_dir / f"{username}_watchlist_available.txt", "w") as f:
        f.write(format_film_list(watchlist_available, f"# {username}'s Watchlist - Available Locally"))
    print(f"  Watchlist available: {len(watchlist_available)} films", file=sys.stderr)

    with open(data_dir / f"{username}_watchlist_missing.txt", "w") as f:
        f.write(format_film_list(watchlist_missing, f"# {username}'s Watchlist - Not Available Locally"))
    print(f"  Watchlist missing: {len(watchlist_missing)} films", file=sys.stderr)

    with open(data_dir / f"{username}_undiscovered.txt", "w") as f:
        f.write(format_film_list(undiscovered, f"# {username}'s Undiscovered Films"))
    print(f"  Undiscovered: {len(undiscovered)} films", file=sys.stderr)

    return {"watched": watched, "watchlist": watchlist}


def process_pair(user1: str, user2: str, user_data: dict, local_movies: list[dict], data_dir: Path) -> None:
    """Generate pairwise watchlist intersection outputs."""
    print(f"Processing pair: {user1} + {user2}", file=sys.stderr)

    watchlist1 = user_data[user1]["watchlist"]
    watchlist2 = user_data[user2]["watchlist"]
    watched1 = user_data[user1]["watched"]
    watched2 = user_data[user2]["watched"]

    if not watchlist1 or not watchlist2:
        print(f"  Skipping (missing watchlist data)", file=sys.stderr)
        return

    # Find intersection of watchlists
    shared_available = []
    shared_missing = []

    for film1 in watchlist1:
        # Check if film is on user2's watchlist too
        if not find_in_list(film1, watchlist2):
            continue

        # Skip if either user has already watched it
        if find_in_list(film1, watched1) or find_in_list(film1, watched2):
            continue

        local_match = find_local_match(film1, local_movies)
        if local_match:
            shared_available.append((film1.get("year"), film1["title"]))
        else:
            shared_missing.append((film1.get("year"), film1["title"]))

    # Sort names for consistent filename
    pair_name = "_".join(sorted([user1, user2]))

    with open(data_dir / f"{pair_name}_shared_watchlist_available.txt", "w") as f:
        f.write(format_film_list(
            shared_available,
            f"# {user1} + {user2} Shared Watchlist - Available Locally"
        ))
    print(f"  Shared available: {len(shared_available)} films", file=sys.stderr)

    with open(data_dir / f"{pair_name}_shared_watchlist_missing.txt", "w") as f:
        f.write(format_film_list(
            shared_missing,
            f"# {user1} + {user2} Shared Watchlist - Not Available Locally"
        ))
    print(f"  Shared missing: {len(shared_missing)} films", file=sys.stderr)


def main() -> None:
    config = load_config()
    data_dir = get_data_dir()

    users = config.get("letterboxd_users", [])
    if not users and config.get("letterboxd_username"):
        users = [config["letterboxd_username"]]

    ap = argparse.ArgumentParser(description="Generate per-user and pairwise film lists")
    ap.add_argument("--users", nargs="+", default=users, help="Letterboxd usernames")
    args = ap.parse_args()

    if not args.users:
        print("Error: No users specified")
        raise SystemExit(1)

    local_movies = get_local_movies(data_dir)
    print(f"Local library: {len(local_movies)} movies", file=sys.stderr)

    # Process each user
    user_data = {}
    for username in args.users:
        user_data[username] = process_user(username, local_movies, data_dir)

    # Process pairs (if more than one user)
    if len(args.users) > 1:
        print("", file=sys.stderr)
        for user1, user2 in itertools.combinations(args.users, 2):
            process_pair(user1, user2, user_data, local_movies, data_dir)

    print("Done", file=sys.stderr)


if __name__ == "__main__":
    main()
