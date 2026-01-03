# Media Library Backup

Daily backups of your media library and Letterboxd data for multiple users, version controlled in git.

## Features

- Scrapes watched films and watchlists from Letterboxd (multi-user)
- Scans local media directories for movies and TV shows
- Generates per-user filtered lists:
  - **Watchlist available**: Films on your watchlist that you already have locally
  - **Undiscovered**: Local films you haven't watched and aren't on your watchlist

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                  # install deps
uv sync --extra fuzzy    # optional: faster matching
```

### Configure

```bash
mkdir -p data
cp config.example.json data/config.json
# Edit data/config.json
```

### Private data repo

```bash
cd data
git init
git remote add origin git@github.com:you/media-backup-data.git
```

## Commands

```bash
uv run letterboxd   # Scrape Letterboxd for all users
uv run snapshot     # Scan local media
uv run unwatched    # Generate per-user filtered lists
./cron_backup.sh    # Run all + commit/push
```

## Configuration

`data/config.json`:

```json
{
  "letterboxd_users": ["user1", "user2"],
  "media_directories": {
    "movies": "/path/to/movies",
    "tv": "/path/to/tv"
  }
}
```

## Output Files

Per-user (in `data/`):
- `{user}_watched.json` - Films watched on Letterboxd
- `{user}_watchlist.json` - Letterboxd watchlist
- `{user}_watchlist_available.txt` - Watchlist films available locally
- `{user}_undiscovered.txt` - Local films not watched, not on watchlist

Shared:
- `media_library.json` - Full local media metadata
- `media_list.txt` - Human-readable local media list

## Cron

```
0 4 * * * /path/to/cron_backup.sh >> /path/to/cron.log 2>&1
```
