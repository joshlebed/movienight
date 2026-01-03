"""Configuration loading for media backup tools."""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_FILENAME = "config.json"
DATA_DIR_NAME = "data"


def get_repo_root() -> Path:
    """Get the repository root directory."""
    return Path(__file__).parent.parent.parent


def get_data_dir() -> Path:
    """Get the data directory path."""
    return get_repo_root() / DATA_DIR_NAME


def load_config() -> dict:
    """Load config from data/config.json."""
    config_path = get_data_dir() / CONFIG_FILENAME
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return json.load(f)


def get_media_directories() -> tuple[Path, Path]:
    """Get media directories from config, with defaults."""
    config = load_config()
    media_dirs = config.get("media_directories", {})

    movies_dir = Path(media_dirs.get("movies", "/mnt/vault/movies"))
    tv_dir = Path(media_dirs.get("tv", "/mnt/vault/tv"))

    return movies_dir, tv_dir


def get_torrents_directory() -> Path | None:
    """Get torrents metadata directory from config.

    Returns None if not configured (torrent matching disabled).
    """
    config = load_config()
    media_dirs = config.get("media_directories", {})

    torrents_path = media_dirs.get("torrents")
    if torrents_path:
        return Path(torrents_path)
    return None


def get_cache_dir() -> Path:
    """Get the cache directory for JSON data files."""
    cache_dir = get_data_dir() / "cache"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir


def get_letterboxd_cache_dir() -> Path:
    """Get the letterboxd cache directory."""
    lb_dir = get_cache_dir() / "letterboxd"
    lb_dir.mkdir(exist_ok=True)
    return lb_dir


def get_reports_dir() -> Path:
    """Get the reports directory for generated output files."""
    reports_dir = get_data_dir() / "reports"
    reports_dir.mkdir(exist_ok=True)
    return reports_dir
