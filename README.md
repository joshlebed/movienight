# Media Library Backup

Daily backups of your media library and Letterboxd data for multiple users, version controlled in git.

## Features

- Scrapes watched films and watchlists from Letterboxd (multi-user)
- 24-hour caching to avoid unnecessary requests
- Scans local media directories for movies and TV shows
- Generates per-user filtered lists
- Generates pairwise watchlist intersections for movie nights

## Quick Start

```bash
make setup                 # Install deps + create data/config.json
# Edit data/config.json with your settings
make backup                # Run full backup
```

## Setup

```bash
make install               # Install dependencies
make install-fuzzy         # With faster fuzzy matching
make setup                 # Create data/ and copy example config
```

Edit `data/config.json`:

```json
{
  "letterboxd_users": ["user1", "user2"],
  "media_directories": {
    "movies": "/path/to/movies",
    "tv": "/path/to/tv"
  }
}
```

### Private data repo

The `data/` directory is gitignored. Initialize it as a separate private repo:

```bash
cd data
git init
git remote add origin git@github.com:you/media-backup-data.git
```

## Commands

```bash
make backup            # Run full backup (letterboxd + snapshot + unwatched)
make letterboxd        # Scrape Letterboxd (uses 24h cache)
make letterboxd-force  # Force fresh scrape
make snapshot          # Scan local media
make unwatched         # Generate filtered lists
```

Or use `uv run` directly:

```bash
uv run letterboxd [--force] [--users user1 user2]
uv run snapshot
uv run unwatched [--users user1 user2]
```

## Output Files

### Per-user

| File | Description |
|------|-------------|
| `{user}_watched.json` | Films watched on Letterboxd |
| `{user}_watchlist.json` | Letterboxd watchlist |
| `{user}_watchlist_available.txt` | Watchlist films available locally |
| `{user}_watchlist_missing.txt` | Watchlist films NOT available locally |
| `{user}_undiscovered.txt` | Local films not watched, not on watchlist |

### Pairwise (for each pair of users)

| File | Description |
|------|-------------|
| `{user1}_{user2}_shared_watchlist_available.txt` | Shared watchlist, available locally |
| `{user1}_{user2}_shared_watchlist_missing.txt` | Shared watchlist, NOT available locally |

### Shared

| File | Description |
|------|-------------|
| `media_library.json` | Full local media metadata |
| `media_list.txt` | Human-readable local media list |

## Cron

```
0 4 * * * /path/to/cron_backup.sh >> /path/to/cron.log 2>&1
```

## Development

```bash
make install-dev       # Install dev dependencies
make lint              # Run linter
make format            # Format code
make clean             # Remove cache files
```
