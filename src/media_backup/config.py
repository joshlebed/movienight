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
