#!/usr/bin/env python3
"""
Fetch Letterboxd and IMDb ratings for films.
Results are cached for 6 months to avoid repeated requests.
Uses concurrent fetching for speed.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from media_backup.config import get_cache_dir

RATINGS_CACHE_FILE = "ratings_cache.json"
CACHE_TTL_DAYS = 180  # 6 months
MAX_WORKERS = 5  # Concurrent requests (be respectful to servers)
RATE_LIMIT_DELAY = 0.3  # Minimum seconds between requests per domain


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


# Global rate limiter
rate_limiter = RateLimiter()


def get_cache_path() -> Path:
    return get_cache_dir() / RATINGS_CACHE_FILE


def load_cache() -> dict:
    path = get_cache_path()
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_cache(cache: dict) -> None:
    path = get_cache_path()
    with open(path, "w") as f:
        json.dump(cache, f, indent=2)
        f.write("\n")


def is_cache_entry_valid(entry: dict) -> bool:
    """Check if cache entry is still valid (not expired)."""
    fetched_at = entry.get("fetched_at")
    if not fetched_at:
        # Old cache entries without timestamp - consider valid but will be refreshed eventually
        return True
    try:
        fetched_date = datetime.fromisoformat(fetched_at)
        return datetime.now() - fetched_date < timedelta(days=CACHE_TTL_DAYS)
    except (ValueError, TypeError):
        return True


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def fetch_letterboxd_rating(session: requests.Session, film_url: str) -> float | None:
    """Fetch the average rating from a Letterboxd film page."""
    try:
        rate_limiter.wait("letterboxd.com")
        r = session.get(film_url, timeout=15)
        if r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        # Look for the rating in meta tags first (most reliable)
        meta_rating = soup.find("meta", {"name": "twitter:data2"})
        if meta_rating:
            content = meta_rating.get("content", "")
            match = re.search(r"([\d.]+)\s*out of\s*5", content)
            if match:
                return float(match.group(1))

        # Fallback: look for rating in the page
        rating_elem = soup.select_one("a.tooltip.display-rating")
        if rating_elem:
            text = rating_elem.get_text(strip=True)
            try:
                return float(text)
            except ValueError:
                pass

        # Another fallback: check the histogram section
        rating_elem = soup.select_one("span.average-rating a")
        if rating_elem:
            text = rating_elem.get_text(strip=True)
            try:
                return float(text)
            except ValueError:
                pass

    except Exception as e:
        print(f"  Error fetching {film_url}: {e}", file=sys.stderr)

    return None


def fetch_imdb_rating(
    session: requests.Session, title: str, year: int | None
) -> tuple[float | None, str | None]:
    """Fetch IMDb rating by searching for the film. Returns (rating, imdb_id)."""
    try:
        # Search IMDb
        query = f"{title} {year}" if year else title
        search_url = (
            f"https://www.imdb.com/find/?q={requests.utils.quote(query)}&s=tt&ttype=ft"
        )

        rate_limiter.wait("imdb.com")
        r = session.get(search_url, timeout=15)
        if r.status_code != 200:
            return None, None

        soup = BeautifulSoup(r.text, "html.parser")

        # Find first result
        result = soup.select_one("a.ipc-metadata-list-summary-item__t")
        if not result:
            return None, None

        href = result.get("href", "")
        imdb_match = re.search(r"/title/(tt\d+)/", href)
        if not imdb_match:
            return None, None

        imdb_id = imdb_match.group(1)

        # Fetch the title page for rating
        title_url = f"https://www.imdb.com/title/{imdb_id}/"
        rate_limiter.wait("imdb.com")
        r = session.get(title_url, timeout=15)
        if r.status_code != 200:
            return None, imdb_id

        soup = BeautifulSoup(r.text, "html.parser")

        # Look for rating in JSON-LD first (most reliable)
        script = soup.find("script", {"type": "application/ld+json"})
        if script:
            try:
                data = json.loads(script.string)
                if "aggregateRating" in data:
                    rating = data["aggregateRating"].get("ratingValue")
                    if rating:
                        return float(rating), imdb_id
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        # Fallback: look for rating element
        rating_elem = soup.select_one(
            "span.sc-eb51e184-1, div[data-testid='hero-rating-bar__aggregate-rating__score'] span"
        )
        if rating_elem:
            text = rating_elem.get_text(strip=True)
            try:
                rating = float(text.split("/")[0])
                return rating, imdb_id
            except (ValueError, IndexError):
                pass

        return None, imdb_id

    except Exception as e:
        print(f"  Error fetching IMDb for '{title}': {e}", file=sys.stderr)

    return None, None


def fetch_ratings_for_film(
    film: dict, session: requests.Session
) -> tuple[str, dict]:
    """Fetch both LB and IMDb ratings for a film. Returns (slug, ratings_dict)."""
    slug = film.get("film_slug", "")
    film_url = film.get("film_url", "")
    title = film.get("title", "")
    year = film.get("year")

    # Fetch both ratings (they use different domains so can overlap somewhat)
    lb_rating = fetch_letterboxd_rating(session, film_url) if film_url else None
    imdb_rating, imdb_id = fetch_imdb_rating(session, title, year)

    return slug, {
        "letterboxd_rating": lb_rating,
        "imdb_rating": imdb_rating,
        "imdb_id": imdb_id,
        "fetched_at": datetime.now().isoformat(),
    }


def enrich_films_with_ratings(
    films: list[dict],
    force: bool = False,
    max_workers: int = MAX_WORKERS,
) -> list[dict]:
    """Enrich a list of films with ratings using concurrent fetching."""
    cache = load_cache()
    cache_lock = threading.Lock()

    # Separate films into cached and uncached
    to_fetch = []
    cached_count = 0

    for film in films:
        slug = film.get("film_slug", "")
        if not slug:
            continue

        if not force and slug in cache and is_cache_entry_valid(cache[slug]):
            # Use cached data
            cached_count += 1
            cached = cache[slug]
            film["letterboxd_rating"] = cached.get("letterboxd_rating")
            film["imdb_rating"] = cached.get("imdb_rating")
            film["imdb_id"] = cached.get("imdb_id")
        else:
            to_fetch.append(film)

    if cached_count > 0:
        print(f"  Using cache for {cached_count} films", file=sys.stderr)

    if not to_fetch:
        return films

    print(
        f"  Fetching ratings for {len(to_fetch)} films ({max_workers} workers)...",
        file=sys.stderr,
    )

    # Create a session per thread for connection pooling
    session_local = threading.local()

    def get_session() -> requests.Session:
        if not hasattr(session_local, "session"):
            session_local.session = create_session()
        return session_local.session

    def fetch_with_session(film: dict) -> tuple[str, dict, dict]:
        session = get_session()
        slug, ratings = fetch_ratings_for_film(film, session)
        return slug, ratings, film

    # Fetch concurrently
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_with_session, film): film for film in to_fetch}

        for future in as_completed(futures):
            try:
                slug, ratings, film = future.result()
                completed += 1

                # Update film
                film["letterboxd_rating"] = ratings.get("letterboxd_rating")
                film["imdb_rating"] = ratings.get("imdb_rating")
                film["imdb_id"] = ratings.get("imdb_id")

                # Update cache (thread-safe)
                with cache_lock:
                    cache[slug] = ratings

                # Progress update
                title = film.get("title", "")
                lb = ratings.get("letterboxd_rating")
                imdb = ratings.get("imdb_rating")
                lb_str = f"{lb:.1f}" if lb else "-.-"
                imdb_str = f"{imdb:.1f}" if imdb else "-.-"
                print(
                    f"  [{completed}/{len(to_fetch)}] {title}: LB {lb_str}, IMDb {imdb_str}",
                    file=sys.stderr,
                )

                # Save cache periodically
                if completed % 10 == 0:
                    with cache_lock:
                        save_cache(cache)

            except Exception as e:
                film = futures[future]
                print(
                    f"  Error processing {film.get('title', '?')}: {e}", file=sys.stderr
                )

    # Final cache save
    save_cache(cache)
    print(f"  Fetched ratings for {completed} films", file=sys.stderr)

    return films
