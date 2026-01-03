#!/usr/bin/env python3
"""
Scrape Letterboxd watched films and watchlists for multiple users.
Includes 24-hour caching to avoid unnecessary requests.

Usage:
  uv run letterboxd
  uv run letterboxd --users user1 user2
  uv run letterboxd --force  # ignore cache
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from media_backup.config import get_data_dir, load_config

LETTERBOXD_BASE = "https://letterboxd.com"
CACHE_MAX_AGE_HOURS = 24


def get_file_age_hours(path: Path) -> float | None:
    """Get file age in hours, or None if file doesn't exist."""
    if not path.exists():
        return None
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    age = datetime.now() - mtime
    return age.total_seconds() / 3600


def is_cache_valid(path: Path, max_age_hours: float = CACHE_MAX_AGE_HOURS) -> bool:
    """Check if cached file is still valid."""
    age = get_file_age_hours(path)
    if age is None:
        return False
    return age < max_age_hours


def build_films_url(username: str, page: int) -> str:
    """Build URL for user's watched films page."""
    if page == 1:
        return f"{LETTERBOXD_BASE}/{username}/films/"
    return f"{LETTERBOXD_BASE}/{username}/films/page/{page}/"


def build_watchlist_url(username: str, page: int) -> str:
    """Build URL for user's watchlist page."""
    if page == 1:
        return f"{LETTERBOXD_BASE}/{username}/watchlist/"
    return f"{LETTERBOXD_BASE}/{username}/watchlist/page/{page}/"


def fetch_html(session: requests.Session, url: str, timeout: int = 30) -> str:
    """Fetch HTML from URL, returning empty string on 404."""
    r = session.get(url, timeout=timeout)
    if r.status_code == 404:
        return ""
    r.raise_for_status()
    return r.text


def parse_films_from_page(html: str) -> list[dict]:
    """Parse films from a Letterboxd page."""
    soup = BeautifulSoup(html, "html.parser")
    posters = soup.select("div.react-component[data-item-slug]")

    films: list[dict] = []
    for p in posters:
        slug = (p.get("data-item-slug") or "").strip()
        full_name = (p.get("data-item-name") or p.get("data-item-full-display-name") or "").strip()

        if not slug or not full_name:
            continue

        film_url = f"{LETTERBOXD_BASE}/film/{slug}/"

        year: int | None = None
        title = full_name

        m = re.search(r"^(.+?)\s*\((\d{4})\)$", full_name)
        if m:
            title = m.group(1).strip()
            try:
                year = int(m.group(2))
            except ValueError:
                pass

        films.append({
            "title": title,
            "year": year,
            "film_slug": slug,
            "film_url": film_url,
        })

    return films


def scrape_films(
    session: requests.Session,
    username: str,
    url_builder: callable,
    delay: float,
    max_pages: int | None = None,
) -> list[dict]:
    """Scrape all films from paginated Letterboxd pages."""
    seen: dict[str, dict] = {}
    page = 1

    while True:
        if max_pages is not None and page > max_pages:
            break

        url = url_builder(username, page)
        html = fetch_html(session, url)

        if not html.strip():
            break

        films = parse_films_from_page(html)
        if not films:
            break

        for f in films:
            seen.setdefault(f["film_slug"], f)

        page += 1
        time.sleep(delay)

    return sorted(
        seen.values(),
        key=lambda f: (f["title"].lower(), f["year"] or 0, f["film_slug"]),
    )


def create_session() -> requests.Session:
    """Create a requests session with appropriate headers."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "letterboxd-scraper/1.0 (+https://github.com/joshlebed/backup_movie_list)",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def write_json(path: Path, data: list[dict]) -> None:
    """Write data to JSON file."""
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def scrape_user(
    username: str,
    data_dir: Path,
    delay: float,
    force: bool = False,
    max_pages: int | None = None,
) -> tuple[list[dict], list[dict]]:
    """Scrape both watched films and watchlist for a user, with caching."""
    watched_path = data_dir / f"{username}_watched.json"
    watchlist_path = data_dir / f"{username}_watchlist.json"

    # Check cache
    watched_valid = is_cache_valid(watched_path)
    watchlist_valid = is_cache_valid(watchlist_path)

    if not force and watched_valid and watchlist_valid:
        watched_age = get_file_age_hours(watched_path)
        print(f"  Using cached data ({watched_age:.1f}h old)", file=sys.stderr)
        with open(watched_path) as f:
            watched = json.load(f)
        with open(watchlist_path) as f:
            watchlist = json.load(f)
        return watched, watchlist

    # Scrape fresh data
    session = create_session()

    print(f"  Scraping watched films...", file=sys.stderr)
    watched = scrape_films(session, username, build_films_url, delay, max_pages)
    print(f"  Found {len(watched)} watched films", file=sys.stderr)

    print(f"  Scraping watchlist...", file=sys.stderr)
    watchlist = scrape_films(session, username, build_watchlist_url, delay, max_pages)
    print(f"  Found {len(watchlist)} watchlist films", file=sys.stderr)

    # Save to cache
    write_json(watched_path, watched)
    write_json(watchlist_path, watchlist)

    return watched, watchlist


def main() -> None:
    config = load_config()
    data_dir = get_data_dir()

    default_users = config.get("letterboxd_users", [])
    if not default_users and config.get("letterboxd_username"):
        default_users = [config["letterboxd_username"]]

    ap = argparse.ArgumentParser(description="Scrape Letterboxd watched films and watchlists")
    ap.add_argument(
        "--users",
        nargs="+",
        default=default_users,
        help="Letterboxd usernames (default: from config.json)",
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=0.75,
        help="Delay between page requests (seconds)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Force refresh, ignoring cache",
    )
    ap.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional cap for pages per list (debugging)",
    )
    args = ap.parse_args()

    if not args.users:
        print("Error: No users specified. Set letterboxd_users in config.json or use --users")
        raise SystemExit(1)

    for username in args.users:
        print(f"Processing {username}:", file=sys.stderr)
        scrape_user(username, data_dir, delay=args.delay, force=args.force, max_pages=args.max_pages)

    print("Done", file=sys.stderr)


if __name__ == "__main__":
    main()
