# Media Library Backup

Daily backups of your media library and Letterboxd watch history, version controlled in git.

## Features

- Scrapes watched films from your public Letterboxd profile
- Scans local media directories for movies and TV shows
- Computes which films you haven't watched yet (fuzzy matching)
- Commits and pushes changes daily via cron

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
# Install dependencies
uv sync

# Optional: faster fuzzy matching
uv sync --extra fuzzy
```

### Configure

```bash
# Create data directory and config
mkdir -p data
cp config.example.json data/config.json
# Edit data/config.json with your settings
```

### Set up private data repo

The `data/` directory is gitignored. Initialize it as a separate private repo:

```bash
cd data
git init
git remote add origin git@github.com:you/media-backup-data.git
```

## Commands

```bash
uv run letterboxd   # Scrape Letterboxd -> data/films_already_watched.json
uv run snapshot     # Scan media -> data/media_library.json, data/media_list.txt
uv run unwatched    # Compare -> data/unwatched.txt
./cron_backup.sh    # Run all, commit and push data repo
```

## Cron

```
0 4 * * * /path/to/backup_movie_list/cron_backup.sh >> /path/to/backup_movie_list/cron.log 2>&1
```

## Configuration

`data/config.json`:

```json
{
  "letterboxd_username": "your_username",
  "media_directories": {
    "movies": "/path/to/movies",
    "tv": "/path/to/tv"
  }
}
```

## File Structure

```
backup_movie_list/          # Public repo (code)
├── src/media_backup/       # Python package
├── config.example.json     # Example config
├── cron_backup.sh          # Daily backup script
└── data/                   # Private repo (gitignored)
    ├── config.json         # Your config
    ├── films_already_watched.json
    ├── media_library.json
    ├── media_list.txt
    └── unwatched.txt
```
