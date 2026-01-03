# Media Library Backup

Track your Letterboxd watchlist against your local media library. Find out what's ready to watch, what you need to download, and discover films you already have but haven't seen.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) - Python package manager
- git
- A [Letterboxd](https://letterboxd.com) account

## Quick Start

```bash
git clone https://github.com/you/backup_movie_list.git
cd backup_movie_list
make setup      # Interactive wizard
make backup     # Run first backup
```

The setup wizard will:
1. Ask for your Letterboxd username(s)
2. Configure paths to your local media
3. Optionally set up a private git repo to version your backups

## Restoring on a New Machine

If you already have a backup repo from a previous setup:

```bash
git clone https://github.com/you/backup_movie_list.git
cd backup_movie_list
make setup      # Choose "Restore from existing backup repo"
```

## What You Get

After running `make backup`, you'll find markdown reports in `data/reports/`:

| Report | What it shows |
|--------|---------------|
| `{user}.md` | Your watchlist (available + missing), plus unwatched films in your library |
| `shared_{user1}_{user2}.md` | Films both users want to watch (great for movie night) |

## Commands

```bash
make backup            # Full backup (scrape + scan + generate reports)
make letterboxd        # Just scrape Letterboxd (cached 24h)
make letterboxd-force  # Force fresh scrape
make snapshot          # Just scan local media
make unwatched         # Just regenerate reports
```

## Automation

Run daily via cron:

```
0 4 * * * /path/to/cron_backup.sh >> /path/to/cron.log 2>&1
```

## Advanced

<details>
<summary>Manual configuration</summary>

Edit `data/config.json` directly:

```json
{
  "letterboxd_users": ["user1", "user2"],
  "media_directories": {
    "movies": "/path/to/movies",
    "tv": "/path/to/tv",
    "torrents": "/path/to/torrent/metadata"
  }
}
```

The `torrents` directory is optional - if set, generates magnet links from `.torrent` files.

</details>

<details>
<summary>Data directory structure</summary>

```
data/
├── config.json              # Your settings
├── cache/                   # Scraped data (auto-generated)
│   ├── letterboxd/          # Watched + watchlist JSON per user
│   ├── media_library.json   # Local media scan
│   └── ratings_cache.json   # Film ratings
└── reports/                 # Human-readable output
    ├── {user}.md
    └── shared_{user1}_{user2}.md
```

</details>

<details>
<summary>Private backup repo</summary>

The `data/` directory is gitignored. The setup wizard can configure it as a separate private repo, or do it manually:

```bash
cd data
git init
git remote add origin git@github.com:you/media-backup-data.git
```

</details>

<details>
<summary>Development</summary>

```bash
make install-dev    # Install dev tools
make lint           # Run linter
make format         # Format code
```

</details>
