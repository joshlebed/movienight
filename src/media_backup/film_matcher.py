"""Film matching and ID caching.

Maps local movie folders to Letterboxd slugs and IMDB/TMDB IDs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

try:
    from rapidfuzz import fuzz as rapidfuzz

    USE_RAPIDFUZZ = True
except ImportError:
    USE_RAPIDFUZZ = False

from media_backup.config import (
    get_cache_dir,
    get_film_id_cache_path,
    get_letterboxd_cache_dir,
    get_manual_overrides_path,
)


# Rate limiter for web requests
RATE_LIMIT_DELAY = 0.5


class RateLimiter:
    """Thread-safe rate limiter per domain."""

    def __init__(self, min_delay: float = RATE_LIMIT_DELAY):
        self.min_delay = min_delay
        self.last_request: dict[str, float] = {}
        self.lock = threading.Lock()

    def wait(self, domain: str) -> None:
        with self.lock:
            now = time.time()
            last = self.last_request.get(domain, 0)
            wait_time = self.min_delay - (now - last)
            if wait_time > 0:
                time.sleep(wait_time)
            self.last_request[domain] = time.time()


rate_limiter = RateLimiter()


def create_session() -> requests.Session:
    """Create a requests session with browser-like headers."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def search_letterboxd(
    session: requests.Session,
    title: str,
    year: int | None = None,
) -> dict | None:
    """Search Letterboxd for a film and return the best match.

    Uses Letterboxd's autocomplete API which returns JSON results.

    Args:
        session: requests Session
        title: Film title to search for
        year: Optional year to filter results

    Returns:
        Dict with slug, title, year, score if found, None otherwise
    """
    search_title = title.strip()

    # Use the autocomplete API endpoint
    url = "https://letterboxd.com/s/autocompletefilm"

    # Try different query variations
    queries = [search_title]
    if year:
        queries.insert(0, f"{search_title} {year}")

    for query in queries:
        try:
            rate_limiter.wait("letterboxd.com")

            # Set headers for AJAX request
            headers = {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            }

            r = session.get(url, params={"q": query, "limit": 10}, headers=headers, timeout=15)
            if r.status_code != 200:
                continue

            data = r.json()
            if not data.get("result") or not data.get("data"):
                continue

            # Check results
            for film in data["data"]:
                result_title = film.get("name", "")
                result_year = film.get("releaseYear")
                slug = film.get("slug")

                if not slug:
                    continue

                # If we have a year constraint, verify it matches (within 1 year)
                if year and result_year and abs(year - result_year) > 1:
                    continue

                # Verify title similarity
                norm_search = normalize_title(search_title)
                norm_result = normalize_title(result_title)
                score = max(fuzzy_ratio(norm_search, norm_result),
                           token_sort_ratio(norm_search, norm_result))

                # Lower threshold for API search since we trust Letterboxd's ranking
                if score >= 60:
                    return {
                        "slug": slug,
                        "title": result_title,
                        "year": result_year,
                        "score": score,
                    }

        except Exception as e:
            print(f"  Search error for '{query}': {e}", file=sys.stderr)
            continue

    return None

# Cache entry structure for film_id_cache.json:
# {
#   "folder_name": {
#     "letterboxd_slug": "inception",
#     "imdb_id": "tt1375666",
#     "tmdb_id": null,
#     "match_method": "fuzzy",  # fuzzy, manual_override, embedded_metadata
#     "match_score": 95.0,
#     "matched_at": "2026-01-04T10:30:00"
#   }
# }


def load_film_id_cache() -> dict[str, dict]:
    """Load the film ID cache (folder -> IDs mapping)."""
    path = get_film_id_cache_path()
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_film_id_cache(cache: dict[str, dict]) -> None:
    """Save the film ID cache."""
    path = get_film_id_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2))


def load_manual_overrides() -> dict[str, dict]:
    """Load manual match overrides.

    Format: {"folder_name": {"letterboxd_slug": "correct-slug", "note": "optional note"}}
    """
    path = get_manual_overrides_path()
    if path.exists():
        return json.loads(path.read_text())
    return {}


def create_cache_entry(
    letterboxd_slug: str | None,
    imdb_id: str | None,
    tmdb_id: str | None,
    match_method: str,
    match_score: float,
) -> dict:
    """Create a cache entry with timestamp."""
    return {
        "letterboxd_slug": letterboxd_slug,
        "imdb_id": imdb_id,
        "tmdb_id": tmdb_id,
        "match_method": match_method,
        "match_score": match_score,
        "matched_at": datetime.now().isoformat(),
    }


# Fuzzy matching constants and functions
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
    """Calculate fuzzy string similarity ratio (0-100)."""
    if USE_RAPIDFUZZ:
        return rapidfuzz.ratio(s1, s2)
    return SequenceMatcher(None, s1, s2).ratio() * 100


def token_sort_ratio(s1: str, s2: str) -> float:
    """Calculate token sort ratio (word-order independent, 0-100)."""
    if USE_RAPIDFUZZ:
        return rapidfuzz.token_sort_ratio(s1, s2)
    tokens1 = sorted(s1.split())
    tokens2 = sorted(s2.split())
    return SequenceMatcher(None, " ".join(tokens1), " ".join(tokens2)).ratio() * 100


def titles_match(title1: str, title2: str, year1: int | None, year2: int | None) -> tuple[bool, float]:
    """Check if two titles match, returns (match, score)."""
    # Year filter: must be within 1 year
    if year1 and year2 and abs(year1 - year2) > 1:
        return False, 0.0

    norm1 = normalize_title(title1)
    norm2 = normalize_title(title2)

    ratio = fuzzy_ratio(norm1, norm2)
    token_ratio = token_sort_ratio(norm1, norm2)
    score = max(ratio, token_ratio)

    return score >= MATCH_THRESHOLD, score


def get_match_for_folder(
    folder: str,
    title: str,
    year: int | None,
    cache: dict[str, dict],
    overrides: dict[str, dict],
    letterboxd_films: list[dict],
    embedded_imdb: str | None = None,
    session: requests.Session | None = None,
    verbose: bool = False,
) -> dict | None:
    """Get match for a single folder using cascade: cache -> override -> embedded -> fuzzy -> search.

    Args:
        folder: The folder name (cache key)
        title: Extracted title from folder
        year: Extracted year from folder
        cache: The film_id_cache dict
        overrides: Manual overrides dict
        letterboxd_films: List of Letterboxd films to match against
        embedded_imdb: IMDB ID from file metadata, if any
        session: Optional requests session for Letterboxd search
        verbose: If True, log match decisions

    Returns:
        Cache entry dict or None if no match
    """
    # 1. Check cache
    if folder in cache:
        return cache[folder]

    # 2. Check manual overrides
    if folder in overrides:
        override = overrides[folder]
        slug = override.get("letterboxd_slug")
        # Find the Letterboxd film to get IDs
        for film in letterboxd_films:
            if film.get("film_slug") == slug:
                return create_cache_entry(
                    letterboxd_slug=slug,
                    imdb_id=film.get("imdb_id"),
                    tmdb_id=film.get("tmdb_id"),
                    match_method="manual_override",
                    match_score=100.0,
                )
        # Override exists but film not in list - still use the slug
        return create_cache_entry(
            letterboxd_slug=slug,
            imdb_id=None,
            tmdb_id=None,
            match_method="manual_override",
            match_score=100.0,
        )

    # 3. Check embedded IMDB ID
    if embedded_imdb:
        for film in letterboxd_films:
            if film.get("imdb_id") == embedded_imdb:
                return create_cache_entry(
                    letterboxd_slug=film.get("film_slug"),
                    imdb_id=embedded_imdb,
                    tmdb_id=film.get("tmdb_id"),
                    match_method="embedded_metadata",
                    match_score=100.0,
                )

    # 4. Fuzzy match against known Letterboxd films
    best_match = None
    best_score = 0.0

    for film in letterboxd_films:
        film_title = film.get("title", "")
        film_year = film.get("year")

        match, score = titles_match(title, film_title, year, film_year)
        if match and score > best_score:
            best_score = score
            best_match = film

    if best_match:
        return create_cache_entry(
            letterboxd_slug=best_match.get("film_slug"),
            imdb_id=best_match.get("imdb_id"),
            tmdb_id=best_match.get("tmdb_id"),
            match_method="fuzzy",
            match_score=best_score,
        )

    # 5. Search Letterboxd directly for films not in user's watched/watchlist
    if session and title:
        if verbose:
            print(f"  Searching Letterboxd for: {title} ({year})", file=sys.stderr)

        result = search_letterboxd(session, title, year)
        if result:
            return create_cache_entry(
                letterboxd_slug=result["slug"],
                imdb_id=None,  # Will be enriched later
                tmdb_id=None,
                match_method="letterboxd_search",
                match_score=result["score"],
            )

    # 6. No match found
    return None


def match_local_films(
    local_movies: list[dict],
    letterboxd_films: list[dict],
    force_rematch: bool = False,
    verbose: bool = False,
    dry_run: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Match local movies to Letterboxd films and enrich with IDs.

    Args:
        local_movies: List of local movie dicts from media_library.json
        letterboxd_films: Combined list of all Letterboxd films (watched + watchlist)
        force_rematch: If True, ignore cache and rematch all
        verbose: If True, log each match decision
        dry_run: If True, don't save cache

    Returns:
        Tuple of (enriched local_movies, unmatched_movies)
    """
    cache = {} if force_rematch else load_film_id_cache()
    overrides = load_manual_overrides()
    session = create_session()

    matched_count = 0
    unmatched_movies = []

    for movie in local_movies:
        folder = movie.get("folder", "")
        title = movie.get("title", "")
        year = movie.get("year")
        embedded_imdb = movie.get("imdb_id")  # From file metadata

        match = get_match_for_folder(
            folder=folder,
            title=title,
            year=year,
            cache=cache,
            overrides=overrides,
            letterboxd_films=letterboxd_films,
            embedded_imdb=embedded_imdb,
            session=session,
            verbose=verbose,
        )

        if match:
            # Update movie with matched IDs
            movie["letterboxd_slug"] = match.get("letterboxd_slug")
            movie["imdb_id"] = match.get("imdb_id")
            movie["tmdb_id"] = match.get("tmdb_id")
            movie["match_method"] = match.get("match_method")
            movie["match_score"] = match.get("match_score")

            # Update cache
            cache[folder] = match
            matched_count += 1

            if verbose:
                slug = match.get("letterboxd_slug", "?")
                method = match.get("match_method", "?")
                score = match.get("match_score", 0)
                print(f"  {folder} -> {slug} ({method}, score={score:.1f})", file=sys.stderr)
        else:
            unmatched_movies.append(movie)

    # Save updated cache
    if not dry_run:
        save_film_id_cache(cache)

    # Log summary
    total = len(local_movies)
    print(f"Matched {matched_count}/{total} films", file=sys.stderr)

    return local_movies, unmatched_movies


def print_unmatched_report(unmatched_movies: list[dict]) -> None:
    """Print a report of unmatched films with suggestions for manual_overrides.json."""
    if not unmatched_movies:
        print("\nAll films matched!", file=sys.stderr)
        return

    print(f"\n=== Unmatched Films ({len(unmatched_movies)}) ===", file=sys.stderr)
    print("Add entries to manual_overrides.json to fix these:\n", file=sys.stderr)

    for movie in unmatched_movies[:20]:  # Show first 20
        folder = movie.get("folder", "?")
        title = movie.get("title", "?")
        year = movie.get("year", "?")
        print(f'  "{folder}": {{"letterboxd_slug": "SLUG_HERE"}},  # {title} ({year})', file=sys.stderr)

    if len(unmatched_movies) > 20:
        print(f"\n  ... and {len(unmatched_movies) - 20} more", file=sys.stderr)


def load_json(path: Path) -> list[dict]:
    """Load JSON file, return empty list if not found."""
    if not path.exists():
        return []
    return json.loads(path.read_text())


def main() -> None:
    """CLI entry point for match-films command."""
    parser = argparse.ArgumentParser(
        description="Match local movies to Letterboxd films and cache IDs"
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force rematch all films (ignore cache)",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Don't save cache (preview only)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Log each match decision",
    )
    args = parser.parse_args()

    cache_dir = get_cache_dir()
    lb_cache_dir = get_letterboxd_cache_dir()

    # Load local movies from media_library.json
    media_path = cache_dir / "media_library.json"
    if not media_path.exists():
        print(f"Error: {media_path} not found. Run 'snapshot' first.", file=sys.stderr)
        sys.exit(1)

    media = load_json(media_path)
    local_movies = [
        item
        for item in media
        if item.get("type") == "movie" and not item.get("error") and item.get("title")
    ]
    print(f"Local library: {len(local_movies)} movies", file=sys.stderr)

    # Load all Letterboxd films from cache
    all_letterboxd_films: list[dict] = []
    if lb_cache_dir.exists():
        for cache_file in lb_cache_dir.glob("*.json"):
            films = load_json(cache_file)
            all_letterboxd_films.extend(films)

    if not all_letterboxd_films:
        print("Error: No Letterboxd data found. Run 'letterboxd' first.", file=sys.stderr)
        sys.exit(1)

    print(f"Letterboxd films: {len(all_letterboxd_films)}", file=sys.stderr)

    # Run matching
    _, unmatched = match_local_films(
        local_movies,
        all_letterboxd_films,
        force_rematch=args.force,
        verbose=args.verbose,
        dry_run=args.dry_run,
    )

    # Print unmatched report
    print_unmatched_report(unmatched)

    if args.dry_run:
        print("\n(dry-run mode - cache not saved)", file=sys.stderr)


if __name__ == "__main__":
    main()
