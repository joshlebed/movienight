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
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

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
from media_backup.letterboxd_ids import (
    load_letterboxd_film_cache,
    save_letterboxd_film_cache,
)

LETTERBOXD_BASE = "https://letterboxd.com"


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


# Edition modifiers stripped from titles before slug derivation.
EDITION_MODIFIERS = (
    "directors cut",
    "director's cut",
    "theatrical cut",
    "theatrical",
    "extended cut",
    "extended",
    "final cut",
    "remastered",
    "restored",
    "uncut",
    "unrated",
    "special edition",
    "limited edition",
    "criterion",
    "criterion collection",
    "redux",
    "imax",
    "repack",
    "proper",
    "rerip",
    "open matte",
)


def slugify_title(title: str) -> str:
    """Convert a title to a Letterboxd-style slug.

    Letterboxd's convention: lowercase, drop punctuation that's not a separator
    (apostrophes, accents folded), collapse remaining non-alphanumerics to a
    single hyphen. So "All The President's Men" -> "all-the-presidents-men",
    "Amélie" -> "amelie".
    """
    nfkd = unicodedata.normalize("NFKD", title)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Drop apostrophes/quotes outright (don't turn into hyphens).
    cleaned = re.sub(r"[’‘'`\"]", "", ascii_only)
    # Collapse anything else non-alphanumeric to a hyphen.
    slug = re.sub(r"[^a-z0-9]+", "-", cleaned.lower()).strip("-")
    return slug


def strip_edition_modifiers(title: str) -> str:
    """Remove edition/release modifiers like 'Uncut', 'Director's Cut', etc."""
    t = title
    for mod in EDITION_MODIFIERS:
        t = re.sub(rf"\b{re.escape(mod)}\b", "", t, flags=re.IGNORECASE)
    return " ".join(t.split())


def candidate_slugs(title: str, year: int | None) -> list[str]:
    """Generate plausible Letterboxd slug candidates for a (title, year).

    Order matters — first hit wins. Bare slug is tried before year-suffixed
    because Letterboxd uses the bare form for the canonical entry.
    """
    cleaned = strip_edition_modifiers(title)

    # If the title contains "AKA" (alternate title), try each side too.
    forms = [cleaned]
    aka_parts = re.split(r"\s+aka\s+", cleaned, flags=re.IGNORECASE)
    if len(aka_parts) > 1:
        forms.extend(p.strip() for p in aka_parts if p.strip())

    seen: set[str] = set()
    candidates: list[str] = []
    for form in forms:
        base = slugify_title(form)
        if not base or base in seen:
            continue
        seen.add(base)
        candidates.append(base)
        if year:
            candidates.append(f"{base}-{year}")
    return candidates


def parse_film_page(html: str) -> dict:
    """Extract slug-stable fields from a Letterboxd film page.

    Returns a dict with keys: title, year, imdb_id, tmdb_id (any may be None).
    """
    soup = BeautifulSoup(html, "html.parser")

    page_title: str | None = None
    page_year: int | None = None
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        content = og["content"]
        m = re.match(r"(.+?)\s*\((\d{4})\)\s*$", content)
        if m:
            page_title = m.group(1).strip()
            page_year = int(m.group(2))
        else:
            page_title = content.strip()

    if page_year is None:
        yr_link = soup.find("a", href=re.compile(r"/films/year/\d{4}"))
        if yr_link:
            m = re.search(r"(\d{4})", yr_link.get("href", ""))
            if m:
                page_year = int(m.group(1))

    imdb_id: str | None = None
    imdb_link = soup.find("a", href=re.compile(r"imdb\.com/title/tt\d+"))
    if imdb_link:
        m = re.search(r"(tt\d+)", imdb_link["href"])
        if m:
            imdb_id = m.group(1)

    tmdb_id: str | None = None
    tmdb_link = soup.find("a", href=re.compile(r"themoviedb\.org/movie/\d+"))
    if tmdb_link:
        m = re.search(r"/movie/(\d+)", tmdb_link["href"])
        if m:
            tmdb_id = m.group(1)

    return {
        "title": page_title,
        "year": page_year,
        "imdb_id": imdb_id,
        "tmdb_id": tmdb_id,
    }


def search_letterboxd(
    session: requests.Session,
    title: str,
    year: int | None = None,
    film_page_cache: dict[str, dict] | None = None,
) -> dict | None:
    """Find a Letterboxd film by guessing slugs and validating the film page.

    The autocomplete/search/imdb-redirect endpoints are Cloudflare-protected
    and return 403 to scripted clients, but the per-film pages
    (/film/<slug>/) are reachable. This function generates plausible slugs
    from the title (and year for disambiguation), fetches each candidate
    page, and returns the first one whose year matches (within 1 year).

    Args:
        session: requests Session
        title: Film title to search for
        year: Optional year for disambiguation/validation
        film_page_cache: Optional slug-keyed cache to populate with the
            scraped IMDB/TMDB IDs (so a successful guess seeds the
            letterboxd_films.json cache and avoids a re-fetch later).

    Returns:
        Dict with slug, title, year, imdb_id, tmdb_id, score on success.
    """
    for slug in candidate_slugs(title, year):
        rate_limiter.wait("letterboxd.com")
        try:
            r = session.get(
                f"{LETTERBOXD_BASE}/film/{slug}/",
                timeout=15,
                allow_redirects=False,
            )
        except requests.RequestException as e:
            print(f"  Slug fetch error for '{slug}': {e}", file=sys.stderr)
            continue

        if r.status_code != 200:
            continue

        page = parse_film_page(r.text)
        page_year = page.get("year")

        # Year validation: if both years are known they must be within 1.
        if year and page_year and abs(year - page_year) > 1:
            continue

        # Seed the slug-keyed film cache so enrich_films_with_ids doesn't
        # re-fetch the same page on the next run.
        if film_page_cache is not None:
            film_page_cache[slug] = {
                "imdb_id": page.get("imdb_id"),
                "tmdb_id": page.get("tmdb_id"),
                "title": page.get("title"),
                "year": page_year,
                "scraped_at": datetime.now().isoformat(),
            }

        return {
            "slug": slug,
            "title": page.get("title") or title,
            "year": page_year,
            "imdb_id": page.get("imdb_id"),
            "tmdb_id": page.get("tmdb_id"),
            "score": 100.0,
        }

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
        "uncut",
        "special edition",
        "final cut",
        "criterion",
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
    film_page_cache: dict[str, dict] | None = None,
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

        result = search_letterboxd(session, title, year, film_page_cache=film_page_cache)
        if result:
            return create_cache_entry(
                letterboxd_slug=result["slug"],
                imdb_id=result.get("imdb_id"),
                tmdb_id=result.get("tmdb_id"),
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

    # Slug-keyed cache; passed into search_letterboxd so any page we fetch for
    # slug-guess validation is also persisted as the film page cache.
    film_page_cache = load_letterboxd_film_cache()
    film_page_cache_size = len(film_page_cache)

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
            film_page_cache=film_page_cache,
            verbose=verbose,
        )

        if match:
            # Update movie with matched IDs
            movie["letterboxd_slug"] = match.get("letterboxd_slug")
            movie["imdb_id"] = match.get("imdb_id")
            movie["tmdb_id"] = match.get("tmdb_id")
            movie["match_method"] = match.get("match_method")
            movie["match_score"] = match.get("match_score")

            # Manual overrides assert a canonical Letterboxd identity — pull
            # title/year from there so downstream fuzzy checks (library
            # categorization, display) use the show name, not whatever
            # ffprobe pulled from the largest .mkv (often an episode title
            # for TV-as-film entries).
            if match.get("match_method") == "manual_override":
                slug = match.get("letterboxd_slug")
                for film in letterboxd_films:
                    if film.get("film_slug") == slug:
                        if film.get("title"):
                            movie["title"] = film["title"]
                        if film.get("year"):
                            movie["year"] = film["year"]
                        break

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

    # Save updated caches
    if not dry_run:
        save_film_id_cache(cache)
        if len(film_page_cache) != film_page_cache_size:
            save_letterboxd_film_cache(film_page_cache)

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
