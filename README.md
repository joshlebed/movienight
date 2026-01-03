# Media Library Backup

Automated daily snapshots of movies and TV shows stored on the media server, tracked in git for version history.

## Files

- `media_list.txt` - Current snapshot of all movies and TV shows (auto-generated)
- `snapshot.sh` - Script that scans media directories and generates the list
- `cron_backup.sh` - Wrapper script that runs the snapshot and commits/pushes changes

## Media Directories

- Movies: `/mnt/vault/movies`
- TV Shows: `/mnt/vault/tv`

## How It Works

1. `snapshot.sh` lists all directories in the movies and TV folders, sorted alphabetically
2. `cron_backup.sh` runs the snapshot and commits only if there are changes
3. A cron job runs `cron_backup.sh` daily at 4 AM

## Cron Job

```
0 4 * * * /home/joshlebed/code/backup_movie_list/cron_backup.sh >> /home/joshlebed/code/backup_movie_list/cron.log 2>&1
```

To view the cron job: `crontab -l`

To edit: `crontab -e`

## Manual Usage

Run a snapshot manually:
```bash
./snapshot.sh
```

Run snapshot and commit/push if changed:
```bash
./cron_backup.sh
```

## Output Format

The `media_list.txt` file lists one item per line, sorted alphabetically. This format works well with `git diff` - additions and removals show as clean single-line changes.
