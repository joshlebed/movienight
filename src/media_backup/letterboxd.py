#!/usr/bin/env python3
"""
Scrape a public Letterboxd user's watched films into JSON.

Usage:
  uv run letterboxd
  uv run letterboxd --user USERNAME --delay 0.75
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from media_backup.config import get_data_dir, load_config

LETTERBOXD_BASE = "https://letterboxd.com"


def build_page_url(username: str, page: int) -> str:
    if page == 1:
        return f"{LETTERBOXD_BASE}/{username}/films/"
    return f"{LETTERBOXD_BASE}/{username}/films/page/{page}/"


def fetch_html(session: requests.Session, url: str, timeout: int = 30) -> str:
    r = session.get(url, timeout=timeout)
    # Letterboxd returns 404 after the last page; we treat that as stop.
    if r.status_code == 404:
        return ""
    r.raise_for_status()
    return r.text


def parse_films_from_page(html: str) -> list[dict]:
    """
    Parse films from a Letterboxd /films/ page.

    Letterboxd uses React components with data attributes on
    div.react-component[data-item-slug] elements.
    """
    soup = BeautifulSoup(html, "html.parser")

    posters = soup.select("div.react-component[data-item-slug]")

    films: list[dict] = []

    for p in posters:
        slug = (p.get("data-item-slug") or "").strip()
        full_name = (p.get("data-item-name") or p.get("data-item-full-display-name") or "").strip()

        if not slug or not full_name:
            continue

        film_url = f"{LETTERBOXD_BASE}/film/{slug}/"

        # Parse title and year from full_name like "Marty Supreme (2025)"
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


def scrape_all_films(username: str, delay: float, max_pages: int | None = None) -> list[dict]:
    """Scrape all films from a user's Letterboxd profile."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "letterboxd-films-scraper/1.0 (+https://github.com/joshlebed/backup_movie_list)",
        "Accept-Language": "en-US,en;q=0.9",
    })

    seen: dict[str, dict] = {}

    page = 1
    while True:
        if max_pages is not None and page > max_pages:
            break

        url = build_page_url(username, page)
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

    # Sort by title then year for determinism
    out = sorted(
        seen.values(),
        key=lambda f: (f["title"].lower(), f["year"] or 0, f["film_slug"]),
    )
    return out


def main() -> None:
    config = load_config()

    ap = argparse.ArgumentParser(description="Scrape Letterboxd watched films")
    ap.add_argument(
        "--user",
        default=config.get("letterboxd_username"),
        help="Letterboxd username (default: from config.json)",
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=0.75,
        help="Delay between page requests (seconds)",
    )
    ap.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional cap for pages (debugging)",
    )
    args = ap.parse_args()

    if not args.user:
        print("Error: No username specified. Set letterboxd_username in config.json or use --user")
        raise SystemExit(1)

    print(f"Scraping Letterboxd films for user: {args.user}")

    films = scrape_all_films(args.user, delay=args.delay, max_pages=args.max_pages)

    output_path = get_data_dir() / "films_already_watched.json"
    with open(output_path, "w") as f:
        json.dump(films, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Scraped {len(films)} films")
    print(f"Written to: {output_path}")


if __name__ == "__main__":
    main()
