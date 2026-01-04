#!/usr/bin/env python3
"""
Fetch Letterboxd and IMDb ratings for films.
Uses OMDb API if configured (also provides Rotten Tomatoes + Metacritic).
Falls back to scraping if no API key.
Results are cached for 6 months.
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

from media_backup.config import get_cache_dir, load_config

RATINGS_CACHE_FILE = "ratings_cache.json"
CACHE_TTL_DAYS = 180  # 6 months
MAX_WORKERS = 5
RATE_LIMIT_DELAY = 0.3


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
    """Check if cache entry is still valid."""
    fetched_at = entry.get("fetched_at")
    if not fetched_at:
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

        # Look for the rating in meta tags first
        meta_rating = soup.find("meta", {"name": "twitter:data2"})
        if meta_rating:
            content = meta_rating.get("content", "")
            match = re.search(r"([\d.]+)\s*out of\s*5", content)
            if match:
                return float(match.group(1))

        # Fallback: rating element
        for selector in [
            "a.tooltip.display-rating",
            "span.average-rating a",
        ]:
            elem = soup.select_one(selector)
            if elem:
                try:
                    return float(elem.get_text(strip=True))
                except ValueError:
                    pass

    except Exception as e:
        print(f"  Error fetching {film_url}: {e}", file=sys.stderr)

    return None


def fetch_omdb_ratings(
    session: requests.Session, title: str, year: int | None, api_key: str
) -> dict:
    """Fetch ratings from OMDb API. Returns dict with imdb/rt/metacritic ratings."""
    result = {
        "imdb_rating": None,
        "imdb_id": None,
        "rotten_tomatoes": None,
        "metacritic": None,
    }

    try:
        params = {"apikey": api_key, "t": title, "type": "movie"}
        if year:
            params["y"] = year

        rate_limiter.wait("omdbapi.com")
        r = session.get("https://www.omdbapi.com/", params=params, timeout=15)
        if r.status_code != 200:
            return result

        data = r.json()
        if data.get("Response") != "True":
            return result

        # IMDb rating
        imdb_rating = data.get("imdbRating")
        if imdb_rating and imdb_rating != "N/A":
            try:
                result["imdb_rating"] = float(imdb_rating)
            except ValueError:
                pass

        result["imdb_id"] = data.get("imdbID")

        # Parse other ratings
        for rating in data.get("Ratings", []):
            source = rating.get("Source", "")
            value = rating.get("Value", "")

            if source == "Rotten Tomatoes":
                # "85%" -> 85
                match = re.match(r"(\d+)%", value)
                if match:
                    result["rotten_tomatoes"] = int(match.group(1))

            elif source == "Metacritic":
                # "75/100" -> 75
                match = re.match(r"(\d+)/", value)
                if match:
                    result["metacritic"] = int(match.group(1))

    except Exception as e:
        print(f"  OMDb error for '{title}': {e}", file=sys.stderr)

    return result


def fetch_imdb_rating_scrape(
    session: requests.Session, title: str, year: int | None
) -> tuple[float | None, str | None]:
    """Fetch IMDb rating by scraping. Returns (rating, imdb_id)."""
    try:
        query = f"{title} {year}" if year else title
        search_url = (
            f"https://www.imdb.com/find/?q={requests.utils.quote(query)}&s=tt&ttype=ft"
        )

        rate_limiter.wait("imdb.com")
        r = session.get(search_url, timeout=15)
        if r.status_code != 200:
            return None, None

        soup = BeautifulSoup(r.text, "html.parser")
        result = soup.select_one("a.ipc-metadata-list-summary-item__t")
        if not result:
            return None, None

        href = result.get("href", "")
        imdb_match = re.search(r"/title/(tt\d+)/", href)
        if not imdb_match:
            return None, None

        imdb_id = imdb_match.group(1)

        # Fetch title page
        rate_limiter.wait("imdb.com")
        r = session.get(f"https://www.imdb.com/title/{imdb_id}/", timeout=15)
        if r.status_code != 200:
            return None, imdb_id

        soup = BeautifulSoup(r.text, "html.parser")

        # Look for rating in JSON-LD
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

        return None, imdb_id

    except Exception as e:
        print(f"  Error fetching IMDb for '{title}': {e}", file=sys.stderr)

    return None, None


def fetch_ratings_for_film(
    film: dict, session: requests.Session, omdb_api_key: str | None
) -> tuple[str, dict]:
    """Fetch all ratings for a film. Returns (slug, ratings_dict)."""
    slug = film.get("film_slug", "")
    film_url = film.get("film_url", "")
    title = film.get("title", "")
    year = film.get("year")

    # Letterboxd (always scrape - no API)
    lb_rating = fetch_letterboxd_rating(session, film_url) if film_url else None

    # IMDb + RT + Metacritic
    if omdb_api_key:
        omdb = fetch_omdb_ratings(session, title, year, omdb_api_key)
        imdb_rating = omdb["imdb_rating"]
        imdb_id = omdb["imdb_id"]
        rt_rating = omdb["rotten_tomatoes"]
        metacritic = omdb["metacritic"]
    else:
        imdb_rating, imdb_id = fetch_imdb_rating_scrape(session, title, year)
        rt_rating = None
        metacritic = None

    return slug, {
        "letterboxd_rating": lb_rating,
        "imdb_rating": imdb_rating,
        "imdb_id": imdb_id,
        "rotten_tomatoes": rt_rating,
        "metacritic": metacritic,
        "fetched_at": datetime.now().isoformat(),
    }


def enrich_films_with_ratings(
    films: list[dict],
    force: bool = False,
    max_workers: int = MAX_WORKERS,
) -> list[dict]:
    """Enrich a list of films with ratings using concurrent fetching."""
    config = load_config()
    omdb_api_key = config.get("omdb_api_key")

    if omdb_api_key:
        print("  Using OMDb API for ratings", file=sys.stderr)
    else:
        print("  No OMDb API key - scraping IMDb (slower)", file=sys.stderr)

    cache = load_cache()
    cache_lock = threading.Lock()

    to_fetch = []
    cached_count = 0

    for film in films:
        slug = film.get("film_slug", "")
        if not slug:
            continue

        if not force and slug in cache and is_cache_entry_valid(cache[slug]):
            cached_count += 1
            cached = cache[slug]
            film["letterboxd_rating"] = cached.get("letterboxd_rating")
            film["imdb_rating"] = cached.get("imdb_rating")
            film["imdb_id"] = cached.get("imdb_id")
            film["rotten_tomatoes"] = cached.get("rotten_tomatoes")
            film["metacritic"] = cached.get("metacritic")
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

    session_local = threading.local()

    def get_session() -> requests.Session:
        if not hasattr(session_local, "session"):
            session_local.session = create_session()
        return session_local.session

    def fetch_with_session(film: dict) -> tuple[str, dict, dict]:
        session = get_session()
        slug, ratings = fetch_ratings_for_film(film, session, omdb_api_key)
        return slug, ratings, film

    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_with_session, film): film for film in to_fetch}

        for future in as_completed(futures):
            try:
                slug, ratings, film = future.result()
                completed += 1

                film["letterboxd_rating"] = ratings.get("letterboxd_rating")
                film["imdb_rating"] = ratings.get("imdb_rating")
                film["imdb_id"] = ratings.get("imdb_id")
                film["rotten_tomatoes"] = ratings.get("rotten_tomatoes")
                film["metacritic"] = ratings.get("metacritic")

                with cache_lock:
                    cache[slug] = ratings

                # Progress
                title = film.get("title", "")
                lb = ratings.get("letterboxd_rating")
                imdb = ratings.get("imdb_rating")
                lb_str = f"{lb:.1f}" if lb else "-.-"
                imdb_str = f"{imdb:.1f}" if imdb else "-.-"
                print(
                    f"  [{completed}/{len(to_fetch)}] {title}: LB {lb_str}, IMDb {imdb_str}",
                    file=sys.stderr,
                )

                if completed % 10 == 0:
                    with cache_lock:
                        save_cache(cache)

            except Exception as e:
                film = futures[future]
                print(f"  Error processing {film.get('title', '?')}: {e}", file=sys.stderr)

    save_cache(cache)
    print(f"  Fetched ratings for {completed} films", file=sys.stderr)

    return films
