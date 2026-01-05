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
from media_backup.letterboxd_ids import enrich_films_with_ids
from media_backup.ratings import enrich_films_with_ratings

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

    # Convert matched movies to Letterboxd format for rating enrichment
    # The ratings.py module expects: film_slug, film_url, title, year, imdb_id
    movies_to_enrich = []
    for movie in movies:
        slug = movie.get("letterboxd_slug")
        if slug:
            # Create a film dict in the format enrich_films_with_ratings expects
            film = {
                "film_slug": slug,
                "film_url": f"https://letterboxd.com/film/{slug}/",
                "title": movie.get("title", ""),
                "year": movie.get("year"),
                "imdb_id": movie.get("imdb_id"),  # May have from matcher
            }
            movies_to_enrich.append((movie, film))

    if movies_to_enrich:
        # Extract just the film dicts for enrichment
        films_for_enrichment = [f for _, f in movies_to_enrich]

        # First get IMDB/TMDB IDs from Letterboxd pages (for reliable OMDb lookup)
        films_needing_ids = [f for f in films_for_enrichment if not f.get("imdb_id")]
        if films_needing_ids:
            print(f"  Fetching IMDB IDs for {len(films_needing_ids)} local movies...", file=sys.stderr)
            enrich_films_with_ids(films_needing_ids)

        print(f"  Enriching {len(films_for_enrichment)} local movies with ratings...", file=sys.stderr)
        enrich_films_with_ratings(films_for_enrichment)

        # Copy ratings back to movie dicts
        for movie, film in movies_to_enrich:
            movie["letterboxd_rating"] = film.get("letterboxd_rating")
            movie["imdb_rating"] = film.get("imdb_rating")
            movie["rotten_tomatoes"] = film.get("rotten_tomatoes")
            movie["metacritic"] = film.get("metacritic")
            # Also update imdb_id if we got it from OMDb
            if film.get("imdb_id") and not movie.get("imdb_id"):
                movie["imdb_id"] = film.get("imdb_id")

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
    films: list[tuple[float | None, float | None, int | None, int | None, int | None, str, str | None]],
) -> str:
    """Format films as a markdown table.

    Each film is (lb_rating, imdb_rating, rt_rating, metacritic, year, title, film_slug).
    """
    if not films:
        return "_No films_\n"

    # Sort by Letterboxd rating (highest first), then IMDb, then title
    sorted_films = sorted(
        films,
        key=lambda x: (-(x[0] or 0), -(x[1] or 0), x[5].lower()),
    )

    lines = ["| LB | IMDb | RT | MC | Year | Title | Letterboxd |", "|---:|-----:|---:|---:|:----:|:------|:-----------|"]
    for lb_rating, imdb_rating, rt_rating, metacritic, year, title, film_slug in sorted_films:
        year_str = str(year) if year else "????"
        lb_str = f"{lb_rating:.1f}" if lb_rating else "-"
        imdb_str = f"{imdb_rating:.1f}" if imdb_rating else "-"
        rt_str = f"{rt_rating}%" if rt_rating else "-"
        mc_str = str(metacritic) if metacritic else "-"
        # Escape pipe characters to avoid breaking markdown table
        title_escaped = title.replace("|", "∣")
        lb_url = f"[Link](https://letterboxd.com/film/{film_slug}/)" if film_slug else "-"
        lines.append(f"| {lb_str} | {imdb_str} | {rt_str} | {mc_str} | {year_str} | {title_escaped} | {lb_url} |")

    return "\n".join(lines) + "\n"


def process_user(
    username: str,
    local_movies: list[dict],
    slug_lookup: dict[str, dict],
    lb_cache_dir: Path,
) -> tuple[dict, list, list, list, list]:
    """Process a single user and return data for report generation."""
    print(f"Processing user: {username}", file=sys.stderr)

    watched = load_json(lb_cache_dir / f"{username}_watched.json")
    watchlist = load_json(lb_cache_dir / f"{username}_watchlist.json")

    if not watched and not watchlist:
        print(f"  No data found for {username}, skipping", file=sys.stderr)
        return {"watched": [], "watchlist": []}, [], [], [], []

    print(f"  Watched: {len(watched)}, Watchlist: {len(watchlist)}", file=sys.stderr)

    watchlist_available = []  # (lb_rating, imdb_rating, rt, mc, year, title, film_slug)
    watchlist_missing = []
    library_unwatched = []
    library_watched = []

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
        film_slug = film.get("film_slug")

        local_match = find_local_match_by_slug(film, local_movies, slug_lookup)
        if local_match:
            watchlist_available.append((lb_rating, imdb_rating, rt_rating, metacritic, year, title, film_slug))
        else:
            watchlist_missing.append((lb_rating, imdb_rating, rt_rating, metacritic, year, title, film_slug))

    # Process local movies for library_unwatched and library_watched
    for movie in local_movies:
        title = movie["title"]
        year = movie.get("year")
        movie_dict = {"title": title, "year": year}

        is_watched = find_in_list(movie_dict, watched)
        is_on_watchlist = find_in_list(movie_dict, watchlist)

        lb_rating = movie.get("letterboxd_rating")
        imdb_rating = movie.get("imdb_rating")
        rt_rating = movie.get("rotten_tomatoes")
        metacritic = movie.get("metacritic")
        film_slug = movie.get("letterboxd_slug")

        if is_watched and not is_on_watchlist:
            library_watched.append((lb_rating, imdb_rating, rt_rating, metacritic, year, title, film_slug))
        elif not is_watched and not is_on_watchlist:
            library_unwatched.append((lb_rating, imdb_rating, rt_rating, metacritic, year, title, film_slug))

    print(f"  Watchlist available: {len(watchlist_available)} films", file=sys.stderr)
    print(f"  Watchlist missing: {len(watchlist_missing)} films", file=sys.stderr)
    print(f"  Library unwatched: {len(library_unwatched)} films", file=sys.stderr)
    print(f"  Library watched: {len(library_watched)} films", file=sys.stderr)

    return (
        {"watched": watched, "watchlist": watchlist},
        watchlist_available,
        watchlist_missing,
        library_unwatched,
        library_watched,
    )


def write_user_report(
    username: str,
    watchlist_available: list,
    watchlist_missing: list,
    library_unwatched: list,
    library_watched: list,
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
        "",
        f"## Library Already Watched ({len(library_watched)} films)",
        "",
        "_Films in your local library that you've already seen._",
        "",
        format_film_table(library_watched),
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
    shared_available = []  # (lb_rating, imdb_rating, rt, mc, year, title, film_slug)
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
        film_slug = film1.get("film_slug")

        local_match = find_local_match_by_slug(film1, local_movies, slug_lookup)
        if local_match:
            shared_available.append((lb_rating, imdb_rating, rt_rating, metacritic, year, title, film_slug))
        else:
            shared_missing.append((lb_rating, imdb_rating, rt_rating, metacritic, year, title, film_slug))

    # Find local movies both users have watched (and neither has on watchlist)
    shared_watched = []
    for movie in local_movies:
        title = movie["title"]
        year = movie.get("year")
        movie_dict = {"title": title, "year": year}

        both_watched = find_in_list(movie_dict, watched1) and find_in_list(movie_dict, watched2)
        on_either_watchlist = find_in_list(movie_dict, watchlist1) or find_in_list(movie_dict, watchlist2)

        if both_watched and not on_either_watchlist:
            lb_rating = movie.get("letterboxd_rating")
            imdb_rating = movie.get("imdb_rating")
            rt_rating = movie.get("rotten_tomatoes")
            metacritic = movie.get("metacritic")
            film_slug = movie.get("letterboxd_slug")
            shared_watched.append((lb_rating, imdb_rating, rt_rating, metacritic, year, title, film_slug))

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
        "",
        f"## Library Already Watched Together ({len(shared_watched)} films)",
        "",
        "_Films in the local library that both users have already seen._",
        "",
        format_film_table(shared_watched),
    ]

    report_path = shared_dir / f"{pair_name}.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    print(f"  Shared available: {len(shared_available)} films", file=sys.stderr)
    print(f"  Shared missing: {len(shared_missing)} films", file=sys.stderr)
    print(f"  Shared watched: {len(shared_watched)} films", file=sys.stderr)
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
        film_slug = movie.get("letterboxd_slug")
        films.append((lb_rating, imdb_rating, rt_rating, metacritic, year, title, film_slug))

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
        watched_watchlist, available, missing, unwatched, watched = process_user(
            username, local_movies, slug_lookup, lb_cache_dir
        )
        user_data[username] = {
            "watched_watchlist": watched_watchlist,
            "available": available,
            "missing": missing,
            "unwatched": unwatched,
            "watched": watched,
        }
        write_user_report(username, available, missing, unwatched, watched, solo_dir)

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
