#!/bin/bash
# Polled every 5 min by cron. If the qBit torrent-finished hook
# (qbittorrent-vpn/config/movienight-notify-finished.sh) has touched the flag
# file, run snapshot + unwatched. Skip cleanly if the daily 7 AM backup is
# already running (acquire-or-skip via flock on the same lock cron_backup.sh
# uses).
#
# Wired up by `make install-refresh-cron`. The daily 7 AM cron at
# `cron_backup.sh` is the cold-path backstop, so a missed/skipped poll just
# means the report data is stale until 7 AM.
#
# Latency budget: 0–5 min from torrent finish to refreshed reports.

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLAG=/home/joshlebed/code/qbittorrent-vpn/config/movienight-refresh-pending
LOCK=/tmp/movienight-refresh.lock
LOG="$REPO_DIR/cron-refresh.log"

[ -f "$FLAG" ] || exit 0

ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }
log() { echo "$(ts) $*" >> "$LOG"; }

# Acquire-or-skip. Holding fd 9 keeps the lock until script exit.
exec 9>"$LOCK"
if ! flock -n 9; then
    log "skip: another refresh holds $LOCK (likely the daily 7 AM backup)"
    exit 0
fi

# Remove flag now so any new torrent finishes during this run re-trigger the
# next 5-min poll instead of being absorbed into this in-flight refresh.
rm -f "$FLAG"

# uv lives in ~/.local/bin which isn't on cron's default PATH.
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"

cd "$REPO_DIR"
log "start: snapshot + unwatched"
{
    uv run snapshot
    uv run unwatched
} >> "$LOG" 2>&1
log "done"
