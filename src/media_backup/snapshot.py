#!/usr/bin/env python3
"""
Snapshot script for movies and TV shows.
Extracts metadata from media files using ffprobe and outputs:
- media_library.json: Full metadata for all media
- media_list.txt: Human-readable list with years and titles
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from media_backup.config import get_data_dir, get_media_directories

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts"}

# Patterns that indicate a title is NOT a proper title
INVALID_TITLE_PATTERNS = re.compile(
    r"(2160p|1080p|720p|480p|BluRay|WEB-DL|WEBRip|HDR|REMUX|x264|x265|HEVC|H\.?265|"
    r"^Encoded\s|^Ripped\s|^Created\s)",
    re.IGNORECASE,
)

# Image codecs that are embedded thumbnails, not actual video
IMAGE_CODECS = {"mjpeg", "png", "bmp", "gif", "webp", "tiff"}


def get_primary_video_file(folder: Path) -> Path | None:
    """Find the primary (largest) video file in a folder."""
    video_files = []
    for f in folder.iterdir():
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
            video_files.append(f)

    if not video_files:
        # Check subdirectories one level deep
        for subdir in folder.iterdir():
            if subdir.is_dir():
                for f in subdir.iterdir():
                    if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
                        video_files.append(f)

    if not video_files:
        return None

    return max(video_files, key=lambda f: f.stat().st_size)


def run_ffprobe(file_path: Path) -> dict | None:
    """Run ffprobe and return parsed JSON output."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        print(f"  Warning: ffprobe failed for {file_path}: {e}", file=sys.stderr)
    return None


def parse_title_year_from_string(s: str) -> tuple[str | None, int | None]:
    """Parse title and year from a string (folder name or release name)."""
    # Try to find year in parentheses first: "Title (2024)"
    paren_match = re.search(r"^(.+?)\s*\((\d{4})\)", s)
    if paren_match:
        title = paren_match.group(1).replace(".", " ").replace("_", " ").strip()
        return title, int(paren_match.group(2))

    # Try to find year pattern: "Title.2024.quality..." or "Title 2024 quality..."
    year_match = re.search(r"^(.+?)[.\s]((?:19|20)\d{2})(?:[.\s]|$)", s)
    if year_match:
        title = year_match.group(1).replace(".", " ").replace("_", " ").strip()
        # Capitalize first letter if lowercase
        if title and title[0].islower():
            title = title[0].upper() + title[1:]
        return title, int(year_match.group(2))

    # No year found, just clean up the string as title
    title = re.split(
        r"[.\s](?:2160p|1080p|720p|480p|4K|UHD|BluRay|WEB|HDR)", s, flags=re.IGNORECASE
    )[0]
    title = title.replace(".", " ").replace("_", " ").strip()
    # Remove trailing periods
    title = title.rstrip(".")
    # Capitalize first letter if lowercase
    if title and title[0].islower():
        title = title[0].upper() + title[1:]
    return title if title else None, None


def extract_title_year(format_tags: dict, folder_name: str) -> tuple[str | None, int | None]:
    """Extract title and year, preferring embedded metadata over folder name parsing."""
    embedded_title = format_tags.get("title", "")

    # Check if embedded title looks like a proper title
    if embedded_title and not INVALID_TITLE_PATTERNS.search(embedded_title):
        title, year = parse_title_year_from_string(embedded_title)
        if title:
            # If we got a clean title but no year, try to get year from folder name
            if not year:
                _, folder_year = parse_title_year_from_string(folder_name)
                year = folder_year
            return title, year

    # Fall back to folder name parsing
    return parse_title_year_from_string(folder_name)


def extract_streams_by_type(streams: list) -> dict:
    """Group streams by type and extract relevant fields."""
    video_streams = []
    audio_streams = []
    subtitle_streams = []

    for stream in streams:
        codec_type = stream.get("codec_type")
        codec_name = stream.get("codec_name", "")

        if codec_type == "video":
            # Skip embedded images/thumbnails
            if codec_name.lower() in IMAGE_CODECS:
                continue
            video_streams.append(
                {
                    k: stream.get(k)
                    for k in [
                        "codec_name",
                        "profile",
                        "width",
                        "height",
                        "display_aspect_ratio",
                        "pix_fmt",
                        "color_space",
                        "color_transfer",
                        "color_primaries",
                        "r_frame_rate",
                        "bits_per_raw_sample",
                    ]
                    if stream.get(k) is not None
                }
            )

        elif codec_type == "audio":
            audio_data = {
                k: stream.get(k)
                for k in [
                    "codec_name",
                    "profile",
                    "channels",
                    "channel_layout",
                    "sample_rate",
                    "bit_rate",
                    "bits_per_raw_sample",
                ]
                if stream.get(k) is not None
            }
            # Add language and title from tags
            tags = stream.get("tags", {})
            if tags.get("language"):
                audio_data["language"] = tags["language"]
            if tags.get("title"):
                audio_data["title"] = tags["title"]
            # Add disposition flags
            disp = stream.get("disposition", {})
            if disp.get("default"):
                audio_data["default"] = True
            audio_streams.append(audio_data)

        elif codec_type == "subtitle":
            sub_data = {"codec_name": stream.get("codec_name")}
            tags = stream.get("tags", {})
            if tags.get("language"):
                sub_data["language"] = tags["language"]
            if tags.get("title"):
                sub_data["title"] = tags["title"]
            disp = stream.get("disposition", {})
            if disp.get("default"):
                sub_data["default"] = True
            if disp.get("forced"):
                sub_data["forced"] = True
            subtitle_streams.append(sub_data)

    return {
        "video": video_streams,
        "audio": audio_streams,
        "subtitles": subtitle_streams,
    }


def format_duration(seconds: float) -> str:
    """Format seconds as H:MM:SS or M:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def process_media_folder(folder: Path, media_type: str) -> dict | None:
    """Process a media folder and extract all metadata."""
    folder_name = folder.name

    video_file = get_primary_video_file(folder)
    if not video_file:
        return {
            "folder": folder_name,
            "type": media_type,
            "error": "No video file found",
        }

    probe_data = run_ffprobe(video_file)
    if not probe_data:
        return {
            "folder": folder_name,
            "type": media_type,
            "file": video_file.name,
            "error": "ffprobe failed",
        }

    format_info = probe_data.get("format", {})
    format_tags = format_info.get("tags", {})
    streams = probe_data.get("streams", [])

    # Extract title and year
    title, year = extract_title_year(format_tags, folder_name)

    # File info
    file_size = video_file.stat().st_size
    duration = float(format_info.get("duration", 0)) if format_info.get("duration") else None

    # Build the entry
    entry = {
        "title": title,
        "year": year,
        "type": media_type,
        "folder": folder_name,
        "file": {
            "name": video_file.name,
            "size_bytes": file_size,
            "size": format_size(file_size),
            "duration_seconds": duration,
            "duration": format_duration(duration) if duration else None,
            "format": format_info.get("format_name"),
        },
        "streams": extract_streams_by_type(streams),
    }

    # Add embedded metadata IDs if present
    if format_tags.get("IMDB"):
        entry["imdb_id"] = format_tags["IMDB"]
    if format_tags.get("TMDB"):
        entry["tmdb_id"] = format_tags["TMDB"]

    return entry


def scan_directory(base_dir: Path, media_type: str) -> list:
    """Scan a directory for media folders."""
    if not base_dir.exists():
        print(f"Warning: Directory not found: {base_dir}", file=sys.stderr)
        return []

    items = []
    for folder in sorted(base_dir.iterdir()):
        if folder.is_dir():
            print(f"  Processing: {folder.name}", file=sys.stderr)
            item = process_media_folder(folder, media_type)
            if item:
                items.append(item)

    return items


def generate_human_readable(media_list: list) -> str:
    """Generate a minimal human-readable list with years and titles."""
    lines = []

    movies = sorted(
        [m for m in media_list if m.get("type") == "movie"],
        key=lambda x: (x.get("title") or x.get("folder", "")).lower(),
    )
    tv_shows = sorted(
        [m for m in media_list if m.get("type") == "tv"],
        key=lambda x: (x.get("title") or x.get("folder", "")).lower(),
    )

    if movies:
        lines.append("# Movies")
        lines.append("")
        for item in movies:
            title = item.get("title") or item.get("folder")
            year = item.get("year")
            year_str = str(year) if year else "????"
            lines.append(f"({year_str}) {title}")
        lines.append("")

    if tv_shows:
        lines.append("# TV Shows")
        lines.append("")
        for item in tv_shows:
            title = item.get("title") or item.get("folder")
            year = item.get("year")
            year_str = str(year) if year else "????"
            lines.append(f"({year_str}) {title}")

    return "\n".join(lines)


def main() -> None:
    """Main entry point."""
    output_dir = get_data_dir()

    movies_dir, tv_dir = get_media_directories()

    print("Scanning media library...", file=sys.stderr)

    media_list = []

    print(f"\nScanning movies: {movies_dir}", file=sys.stderr)
    media_list.extend(scan_directory(movies_dir, "movie"))

    print(f"\nScanning TV shows: {tv_dir}", file=sys.stderr)
    media_list.extend(scan_directory(tv_dir, "tv"))

    # Sort deterministically
    media_list.sort(
        key=lambda x: (
            x.get("type", ""),
            (x.get("title") or x.get("folder", "")).lower(),
            x.get("year") or 0,
        )
    )

    # Write JSON
    json_path = output_dir / "media_library.json"
    with open(json_path, "w") as f:
        json.dump(media_list, f, indent=2, ensure_ascii=False)
    print(f"\nJSON saved to: {json_path}", file=sys.stderr)

    # Write human-readable list
    txt_path = output_dir / "media_list.txt"
    with open(txt_path, "w") as f:
        f.write(generate_human_readable(media_list))
    print(f"Human-readable list saved to: {txt_path}", file=sys.stderr)

    print(f"\nTotal items: {len(media_list)}", file=sys.stderr)


if __name__ == "__main__":
    main()
