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
    get_shared_reports_dir,
    get_solo_reports_dir,
    load_config,
)
from media_backup.film_matcher import match_local_films

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


def find_local_match_by_slug(
    film: dict, local_movies: list[dict], slug_lookup: dict[str, dict]
) -> dict | None:
    """Find matching local movie by slug first, then fuzzy match.

    Args:
        film: Letterboxd film dict with film_slug
        local_movies: List of local movie dicts
        slug_lookup: Dict mapping slug -> local movie

    Returns:
        Matching local movie or None
    """
    # Try slug-based lookup first (fast)
    slug = film.get("film_slug")
    if slug and slug in slug_lookup:
        return slug_lookup[slug]

    # Fall back to fuzzy matching
    for movie in local_movies:
        if films_match({"title": movie["title"], "year": movie.get("year")}, film):
            return movie
    return None


def find_local_match(film: dict, local_movies: list[dict]) -> dict | None:
    """Find matching local movie for a film (fuzzy match only)."""
    for movie in local_movies:
        if films_match({"title": movie["title"], "year": movie.get("year")}, film):
            return movie
    return None


def load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def get_local_movies(cache_dir: Path) -> tuple[list[dict], dict[str, dict]]:
    """Load local movies and match them to Letterboxd films.

    Returns:
        Tuple of (movies list, slug_lookup dict mapping slug -> movie)
    """
    media_path = cache_dir / "media_library.json"
    media = load_json(media_path)
    movies = [
        item
        for item in media
        if item.get("type") == "movie" and not item.get("error") and item.get("title")
    ]

    # Build ratings lookup and collect all Letterboxd films from cache files
    lb_cache_dir = cache_dir / "letterboxd"
    ratings_lookup: dict[str, dict] = {}  # normalized_title -> ratings
    all_letterboxd_films: list[dict] = []

    if lb_cache_dir.exists():
        for cache_file in lb_cache_dir.glob("*.json"):
            films = load_json(cache_file)
            for film in films:
                all_letterboxd_films.append(film)

                title = film.get("title", "")
                year = film.get("year")
                if not title:
                    continue

                # Create lookup key
                key = normalize_title(title)
                if year:
                    key = f"{key}:{year}"

                # Store ratings if we have any
                if film.get("letterboxd_rating") or film.get("imdb_rating"):
                    ratings_lookup[key] = {
                        "letterboxd_rating": film.get("letterboxd_rating"),
                        "imdb_rating": film.get("imdb_rating"),
                        "rotten_tomatoes": film.get("rotten_tomatoes"),
                        "metacritic": film.get("metacritic"),
                    }

    # Match local movies to Letterboxd films (adds letterboxd_slug, imdb_id, etc.)
    if all_letterboxd_films:
        movies, _ = match_local_films(movies, all_letterboxd_films)

    # Enrich local movies with ratings
    for movie in movies:
        title = movie.get("title", "")
        year = movie.get("year")
        if not title:
            continue

        # Try with year first, then without
        key_with_year = f"{normalize_title(title)}:{year}" if year else None
        key_without_year = normalize_title(title)

        cached = None
        if key_with_year and key_with_year in ratings_lookup:
            cached = ratings_lookup[key_with_year]
        elif key_without_year in ratings_lookup:
            cached = ratings_lookup[key_without_year]

        if cached:
            movie["letterboxd_rating"] = cached.get("letterboxd_rating")
            movie["imdb_rating"] = cached.get("imdb_rating")
            movie["rotten_tomatoes"] = cached.get("rotten_tomatoes")
            movie["metacritic"] = cached.get("metacritic")

    # Build slug lookup for fast matching
    slug_lookup: dict[str, dict] = {}
    for movie in movies:
        slug = movie.get("letterboxd_slug")
        if slug:
            slug_lookup[slug] = movie

    return movies, slug_lookup


def format_rating(lb_rating: float | None, imdb_rating: float | None) -> str:
    """Format ratings as a compact string."""
    lb_str = f"{lb_rating:.1f}" if lb_rating else "-.-"
    imdb_str = f"{imdb_rating:.1f}" if imdb_rating else "-.-"
    return f"{lb_str} / {imdb_str}"


def format_film_table(
    films: list[tuple[float | None, float | None, int | None, int | None, int | None, str]],
) -> str:
    """Format films as a markdown table.

    Each film is (lb_rating, imdb_rating, rt_rating, metacritic, year, title).
    """
    if not films:
        return "_No films_\n"

    # Sort by Letterboxd rating (highest first), then IMDb, then title
    sorted_films = sorted(
        films,
        key=lambda x: (-(x[0] or 0), -(x[1] or 0), x[5].lower()),
    )

    lines = ["| LB | IMDb | RT | MC | Year | Title |", "|---:|-----:|---:|---:|:----:|:------|"]
    for lb_rating, imdb_rating, rt_rating, metacritic, year, title in sorted_films:
        year_str = str(year) if year else "????"
        lb_str = f"{lb_rating:.1f}" if lb_rating else "-"
        imdb_str = f"{imdb_rating:.1f}" if imdb_rating else "-"
        rt_str = f"{rt_rating}%" if rt_rating else "-"
        mc_str = str(metacritic) if metacritic else "-"
        lines.append(f"| {lb_str} | {imdb_str} | {rt_str} | {mc_str} | {year_str} | {title} |")

    return "\n".join(lines) + "\n"


def process_user(
    username: str,
    local_movies: list[dict],
    slug_lookup: dict[str, dict],
    lb_cache_dir: Path,
) -> tuple[dict, list, list, list]:
    """Process a single user and return data for report generation."""
    print(f"Processing user: {username}", file=sys.stderr)

    watched = load_json(lb_cache_dir / f"{username}_watched.json")
    watchlist = load_json(lb_cache_dir / f"{username}_watchlist.json")

    if not watched and not watchlist:
        print(f"  No data found for {username}, skipping", file=sys.stderr)
        return {"watched": [], "watchlist": []}, [], [], []

    print(f"  Watched: {len(watched)}, Watchlist: {len(watchlist)}", file=sys.stderr)

    watchlist_available = []  # (lb_rating, imdb_rating, rt, mc, year, title)
    watchlist_missing = []
    library_unwatched = []

    # Process watchlist
    for film in watchlist:
        is_watched = find_in_list(film, watched)
        if is_watched:
            continue  # Skip films already watched

        lb_rating = film.get("letterboxd_rating")
        imdb_rating = film.get("imdb_rating")
        rt_rating = film.get("rotten_tomatoes")
        metacritic = film.get("metacritic")
        year = film.get("year")
        title = film["title"]

        local_match = find_local_match_by_slug(film, local_movies, slug_lookup)
        if local_match:
            watchlist_available.append((lb_rating, imdb_rating, rt_rating, metacritic, year, title))
        else:
            watchlist_missing.append((lb_rating, imdb_rating, rt_rating, metacritic, year, title))

    # Process local movies for library_unwatched
    for movie in local_movies:
        title = movie["title"]
        year = movie.get("year")
        movie_dict = {"title": title, "year": year}

        is_watched = find_in_list(movie_dict, watched)
        is_on_watchlist = find_in_list(movie_dict, watchlist)

        if not is_watched and not is_on_watchlist:
            library_unwatched.append((None, None, None, None, year, title))

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
    solo_dir: Path,
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

    report_path = solo_dir / f"{username}.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    print(f"  Report written: {report_path}", file=sys.stderr)


def process_pair(
    user1: str,
    user2: str,
    user_data: dict,
    local_movies: list[dict],
    slug_lookup: dict[str, dict],
    shared_dir: Path,
) -> None:
    """Generate pairwise shared watchlist report."""
    print(f"Processing pair: {user1} + {user2}", file=sys.stderr)

    watchlist1 = user_data[user1]["watched_watchlist"]["watchlist"]
    watchlist2 = user_data[user2]["watched_watchlist"]["watchlist"]
    watched1 = user_data[user1]["watched_watchlist"]["watched"]
    watched2 = user_data[user2]["watched_watchlist"]["watched"]

    if not watchlist1 or not watchlist2:
        print("  Skipping (missing watchlist data)", file=sys.stderr)
        return

    # Find intersection of watchlists
    shared_available = []  # (lb_rating, imdb_rating, rt, mc, year, title)
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
        rt_rating = film1.get("rotten_tomatoes")
        metacritic = film1.get("metacritic")
        year = film1.get("year")
        title = film1["title"]

        local_match = find_local_match_by_slug(film1, local_movies, slug_lookup)
        if local_match:
            shared_available.append((lb_rating, imdb_rating, rt_rating, metacritic, year, title))
        else:
            shared_missing.append((lb_rating, imdb_rating, rt_rating, metacritic, year, title))

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

    report_path = shared_dir / f"{pair_name}.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    print(f"  Shared available: {len(shared_available)} films", file=sys.stderr)
    print(f"  Shared missing: {len(shared_missing)} films", file=sys.stderr)
    print(f"  Report written: {report_path}", file=sys.stderr)


def write_library_report(local_movies: list[dict], reports_dir: Path) -> None:
    """Write a report of the entire media library sorted by Letterboxd rating."""
    # Build film tuples with ratings
    films = []
    for movie in local_movies:
        title = movie.get("title") or movie.get("folder", "Unknown")
        year = movie.get("year")
        lb_rating = movie.get("letterboxd_rating")
        imdb_rating = movie.get("imdb_rating")
        rt_rating = movie.get("rotten_tomatoes")
        metacritic = movie.get("metacritic")
        films.append((lb_rating, imdb_rating, rt_rating, metacritic, year, title))

    lines = [
        "# Media Library",
        "",
        f"_{len(films)} films sorted by Letterboxd rating_",
        "",
        format_film_table(films),
    ]

    report_path = reports_dir / "library.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    print(f"Library report written: {report_path}", file=sys.stderr)


def main() -> None:
    config = load_config()
    cache_dir = get_cache_dir()
    lb_cache_dir = get_letterboxd_cache_dir()
    reports_dir = get_reports_dir()
    solo_dir = get_solo_reports_dir()
    shared_dir = get_shared_reports_dir()

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

    local_movies, slug_lookup = get_local_movies(cache_dir)
    print(f"Local library: {len(local_movies)} movies", file=sys.stderr)
    if slug_lookup:
        print(f"  Matched {len(slug_lookup)} to Letterboxd", file=sys.stderr)

    # Process each user
    user_data = {}
    for username in args.users:
        watched_watchlist, available, missing, unwatched = process_user(
            username, local_movies, slug_lookup, lb_cache_dir
        )
        user_data[username] = {
            "watched_watchlist": watched_watchlist,
            "available": available,
            "missing": missing,
            "unwatched": unwatched,
        }
        write_user_report(username, available, missing, unwatched, solo_dir)

    # Process pairs (if more than one user)
    if len(args.users) > 1:
        print("", file=sys.stderr)
        for user1, user2 in itertools.combinations(args.users, 2):
            process_pair(user1, user2, user_data, local_movies, slug_lookup, shared_dir)

    # Generate library report
    print("", file=sys.stderr)
    write_library_report(local_movies, reports_dir)

    print("\nDone", file=sys.stderr)


if __name__ == "__main__":
    main()
