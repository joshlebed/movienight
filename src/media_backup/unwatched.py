#!/usr/bin/env python3
"""
Generate unified markdown reports comparing local media against Letterboxd data.

Per-user output (reports/{user}.md):
- Watchlist available locally
- Watchlist not available locally
- Library unwatched (local films not watched, not on watchlist)

Pairwise output (reports/shared_{user1}_{user2}.md):
- Shared watchlist available locally
- Shared watchlist not available locally
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from media_backup.config import (
    get_cache_dir,
    get_letterboxd_cache_dir,
    get_reports_dir,
    load_config,
)

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

    for suffix in [
        "directors cut",
        "director's cut",
        "remastered",
        "extended",
        "theatrical",
        "unrated",
        "special edition",
    ]:
        t = t.replace(suffix, "")

    if t.startswith("the "):
        t = t[4:]

    for roman, digit in [
        ("viii", "8"),
        ("vii", "7"),
        ("vi", "6"),
        ("iv", "4"),
        ("iii", "3"),
        ("ii", "2"),
        ("v", "5"),
        ("i", "1"),
    ]:
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

    return (
        fuzzy_ratio(n1, n2) >= MATCH_THRESHOLD
        or token_sort_ratio(n1, n2) >= MATCH_THRESHOLD
    )


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


def get_local_movies(cache_dir: Path) -> list[dict]:
    media_path = cache_dir / "media_library.json"
    media = load_json(media_path)
    return [
        item
        for item in media
        if item.get("type") == "movie" and not item.get("error") and item.get("title")
    ]


def format_rating(lb_rating: float | None, imdb_rating: float | None) -> str:
    """Format ratings as a compact string."""
    lb_str = f"{lb_rating:.1f}" if lb_rating else "-.-"
    imdb_str = f"{imdb_rating:.1f}" if imdb_rating else "-.-"
    return f"{lb_str} / {imdb_str}"


def format_film_table(
    films: list[tuple[float | None, float | None, int | None, str]],
) -> str:
    """Format films as a markdown table. Each film is (lb_rating, imdb_rating, year, title)."""
    if not films:
        return "_No films_\n"

    # Sort by Letterboxd rating (highest first), then IMDb, then title
    sorted_films = sorted(
        films,
        key=lambda x: (-(x[0] or 0), -(x[1] or 0), x[3].lower()),
    )

    lines = ["| LB | IMDb | Year | Title |", "|---:|-----:|:----:|:------|"]
    for lb_rating, imdb_rating, year, title in sorted_films:
        year_str = str(year) if year else "????"
        lb_str = f"{lb_rating:.1f}" if lb_rating else "-.-"
        imdb_str = f"{imdb_rating:.1f}" if imdb_rating else "-.-"
        lines.append(f"| {lb_str} | {imdb_str} | {year_str} | {title} |")

    return "\n".join(lines) + "\n"


def process_user(
    username: str, local_movies: list[dict], lb_cache_dir: Path
) -> tuple[dict, list, list, list]:
    """Process a single user and return data for report generation."""
    print(f"Processing user: {username}", file=sys.stderr)

    watched = load_json(lb_cache_dir / f"{username}_watched.json")
    watchlist = load_json(lb_cache_dir / f"{username}_watchlist.json")

    if not watched and not watchlist:
        print(f"  No data found for {username}, skipping", file=sys.stderr)
        return {"watched": [], "watchlist": []}, [], [], []

    print(f"  Watched: {len(watched)}, Watchlist: {len(watchlist)}", file=sys.stderr)

    watchlist_available = []  # (lb_rating, imdb_rating, year, title)
    watchlist_missing = []
    library_unwatched = []

    # Process watchlist
    for film in watchlist:
        is_watched = find_in_list(film, watched)
        if is_watched:
            continue  # Skip films already watched

        lb_rating = film.get("letterboxd_rating")
        imdb_rating = film.get("imdb_rating")
        year = film.get("year")
        title = film["title"]

        local_match = find_local_match(film, local_movies)
        if local_match:
            watchlist_available.append((lb_rating, imdb_rating, year, title))
        else:
            watchlist_missing.append((lb_rating, imdb_rating, year, title))

    # Process local movies for library_unwatched
    for movie in local_movies:
        title = movie["title"]
        year = movie.get("year")
        movie_dict = {"title": title, "year": year}

        is_watched = find_in_list(movie_dict, watched)
        is_on_watchlist = find_in_list(movie_dict, watchlist)

        if not is_watched and not is_on_watchlist:
            library_unwatched.append((None, None, year, title))

    print(f"  Watchlist available: {len(watchlist_available)} films", file=sys.stderr)
    print(f"  Watchlist missing: {len(watchlist_missing)} films", file=sys.stderr)
    print(f"  Library unwatched: {len(library_unwatched)} films", file=sys.stderr)

    return (
        {"watched": watched, "watchlist": watchlist},
        watchlist_available,
        watchlist_missing,
        library_unwatched,
    )


def write_user_report(
    username: str,
    watchlist_available: list,
    watchlist_missing: list,
    library_unwatched: list,
    reports_dir: Path,
) -> None:
    """Write unified markdown report for a user."""
    lines = [
        f"# {username}'s Film Report",
        "",
        f"## Watchlist - Available Locally ({len(watchlist_available)} films)",
        "",
        format_film_table(watchlist_available),
        "",
        f"## Watchlist - Not Available ({len(watchlist_missing)} films)",
        "",
        format_film_table(watchlist_missing),
        "",
        f"## Library Unwatched ({len(library_unwatched)} films)",
        "",
        "_Films in your local library that you haven't watched and aren't on your watchlist._",
        "",
        format_film_table(library_unwatched),
    ]

    report_path = reports_dir / f"{username}.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    print(f"  Report written: {report_path}", file=sys.stderr)


def process_pair(
    user1: str,
    user2: str,
    user_data: dict,
    local_movies: list[dict],
    reports_dir: Path,
) -> None:
    """Generate pairwise shared watchlist report."""
    print(f"Processing pair: {user1} + {user2}", file=sys.stderr)

    watchlist1 = user_data[user1]["watched_watchlist"]["watchlist"]
    watchlist2 = user_data[user2]["watched_watchlist"]["watchlist"]
    watched1 = user_data[user1]["watched_watchlist"]["watched"]
    watched2 = user_data[user2]["watched_watchlist"]["watched"]

    if not watchlist1 or not watchlist2:
        print(f"  Skipping (missing watchlist data)", file=sys.stderr)
        return

    # Find intersection of watchlists
    shared_available = []  # (lb_rating, imdb_rating, year, title)
    shared_missing = []

    for film1 in watchlist1:
        # Check if film is on user2's watchlist too
        if not find_in_list(film1, watchlist2):
            continue

        # Skip if either user has already watched it
        if find_in_list(film1, watched1) or find_in_list(film1, watched2):
            continue

        lb_rating = film1.get("letterboxd_rating")
        imdb_rating = film1.get("imdb_rating")
        year = film1.get("year")
        title = film1["title"]

        local_match = find_local_match(film1, local_movies)
        if local_match:
            shared_available.append((lb_rating, imdb_rating, year, title))
        else:
            shared_missing.append((lb_rating, imdb_rating, year, title))

    # Sort names for consistent filename
    pair_name = "_".join(sorted([user1, user2]))

    # Write unified shared report
    lines = [
        f"# {user1} + {user2} Shared Watchlist",
        "",
        "_Films both users want to watch (and neither has seen yet)._",
        "",
        f"## Available Locally ({len(shared_available)} films)",
        "",
        format_film_table(shared_available),
        "",
        f"## Not Available ({len(shared_missing)} films)",
        "",
        format_film_table(shared_missing),
    ]

    report_path = reports_dir / f"shared_{pair_name}.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    print(f"  Shared available: {len(shared_available)} films", file=sys.stderr)
    print(f"  Shared missing: {len(shared_missing)} films", file=sys.stderr)
    print(f"  Report written: {report_path}", file=sys.stderr)


def main() -> None:
    config = load_config()
    cache_dir = get_cache_dir()
    lb_cache_dir = get_letterboxd_cache_dir()
    reports_dir = get_reports_dir()

    users = config.get("letterboxd_users", [])
    if not users and config.get("letterboxd_username"):
        users = [config["letterboxd_username"]]

    ap = argparse.ArgumentParser(
        description="Generate unified markdown reports for film lists"
    )
    ap.add_argument("--users", nargs="+", default=users, help="Letterboxd usernames")
    args = ap.parse_args()

    if not args.users:
        print("Error: No users specified")
        raise SystemExit(1)

    local_movies = get_local_movies(cache_dir)
    print(f"Local library: {len(local_movies)} movies", file=sys.stderr)

    # Process each user
    user_data = {}
    for username in args.users:
        watched_watchlist, available, missing, unwatched = process_user(
            username, local_movies, lb_cache_dir
        )
        user_data[username] = {
            "watched_watchlist": watched_watchlist,
            "available": available,
            "missing": missing,
            "unwatched": unwatched,
        }
        write_user_report(username, available, missing, unwatched, reports_dir)

    # Process pairs (if more than one user)
    if len(args.users) > 1:
        print("", file=sys.stderr)
        for user1, user2 in itertools.combinations(args.users, 2):
            process_pair(user1, user2, user_data, local_movies, reports_dir)

    print("\nDone", file=sys.stderr)


if __name__ == "__main__":
    main()
