"""Letterboxd film page scraping for IMDB/TMDB IDs."""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from media_backup.config import get_letterboxd_film_cache_path
from media_backup.ratings import RateLimiter

MAX_WORKERS = 5

LETTERBOXD_BASE = "https://letterboxd.com"

# Cache entry structure for letterboxd_films.json:
# {
#   "slug": {
#     "imdb_id": "tt1375666",
#     "tmdb_id": "27205",
#     "title": "Inception",
#     "year": 2010,
#     "scraped_at": "2026-01-04T10:30:00"
#   }
# }


def load_letterboxd_film_cache() -> dict[str, dict]:
    """Load the Letterboxd film cache (slug -> IDs)."""
    path = get_letterboxd_film_cache_path()
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_letterboxd_film_cache(cache: dict[str, dict]) -> None:
    """Save the Letterboxd film cache."""
    path = get_letterboxd_film_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2))


def scrape_letterboxd_film_page(
    session: requests.Session,
    slug: str,
    rate_limiter: RateLimiter | None = None,
) -> dict | None:
    """Scrape IMDB/TMDB IDs from a Letterboxd film page.

    Letterboxd pages contain links like:
    - <a href="https://www.imdb.com/title/tt0289043/">IMDb</a>
    - <a href="https://www.themoviedb.org/movie/170">TMDb</a>

    Args:
        session: Requests session
        slug: Letterboxd film slug (e.g., "inception")
        rate_limiter: Optional rate limiter

    Returns:
        Dict with imdb_id, tmdb_id, or None if scrape failed
    """
    url = f"{LETTERBOXD_BASE}/film/{slug}/"

    if rate_limiter:
        rate_limiter.wait("letterboxd.com")

    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Failed to fetch {url}: {e}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    result = {
        "imdb_id": None,
        "tmdb_id": None,
        "scraped_at": datetime.now().isoformat(),
    }

    # Find IMDB link
    imdb_link = soup.find("a", href=re.compile(r"imdb\.com/title/tt\d+"))
    if imdb_link:
        match = re.search(r"(tt\d+)", imdb_link["href"])
        if match:
            result["imdb_id"] = match.group(1)

    # Find TMDB link
    tmdb_link = soup.find("a", href=re.compile(r"themoviedb\.org/movie/\d+"))
    if tmdb_link:
        match = re.search(r"/movie/(\d+)", tmdb_link["href"])
        if match:
            result["tmdb_id"] = match.group(1)

    return result


def enrich_films_with_ids(
    films: list[dict],
    force_scrape: bool = False,
) -> list[dict]:
    """Enrich films with IMDB/TMDB IDs from Letterboxd pages.

    Uses cache to avoid re-scraping. Updates films in place.

    Args:
        films: List of film dicts with "film_slug" key
        force_scrape: If True, ignore cache and scrape all

    Returns:
        films list with added imdb_id and tmdb_id fields
    """
    cache = {} if force_scrape else load_letterboxd_film_cache()
    rate_limiter = RateLimiter()
    cache_lock = threading.Lock()

    # Find films that need scraping
    to_scrape = []
    for film in films:
        slug = film.get("film_slug")
        if not slug:
            continue

        if slug in cache:
            # Use cached IDs
            cached = cache[slug]
            film["imdb_id"] = cached.get("imdb_id")
            film["tmdb_id"] = cached.get("tmdb_id")
        else:
            to_scrape.append(film)

    if not to_scrape:
        return films

    print(f"Scraping IDs for {len(to_scrape)} films...")

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    def scrape_film(film: dict) -> tuple[dict, dict | None]:
        slug = film["film_slug"]
        result = scrape_letterboxd_film_page(session, slug, rate_limiter)
        return film, result

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(scrape_film, f): f for f in to_scrape}

        for i, future in enumerate(as_completed(futures)):
            film, result = future.result()
            slug = film["film_slug"]

            if result:
                film["imdb_id"] = result.get("imdb_id")
                film["tmdb_id"] = result.get("tmdb_id")

                # Update cache
                with cache_lock:
                    cache[slug] = {
                        "imdb_id": result.get("imdb_id"),
                        "tmdb_id": result.get("tmdb_id"),
                        "title": film.get("title"),
                        "year": film.get("year"),
                        "scraped_at": result.get("scraped_at"),
                    }

            # Periodic save
            if (i + 1) % 10 == 0:
                with cache_lock:
                    save_letterboxd_film_cache(cache)
                print(f"  Scraped {i + 1}/{len(to_scrape)}...")

    # Final save
    save_letterboxd_film_cache(cache)
    print(f"Scraped IDs for {len(to_scrape)} films")

    return films
