# Media Library Backup
#
# Quick start:
#   make setup    - Interactive setup wizard
#   make backup   - Run backup (scrapes Letterboxd, scans media, generates reports)
#
# The backup uses 6-month caching for Letterboxd data. Use `make backup-force` to bypass.

.PHONY: setup backup backup-local backup-force install-cron uninstall-cron \
        install-refresh-cron uninstall-refresh-cron lint format clean \
        clear-letterboxd-user-data help deploy check

help:
	@echo "make deploy                Push + ssh-pull on mediaserver (laptop-side)"
	@echo "make setup                 Interactive setup wizard"
	@echo "make backup                Run full backup (Letterboxd + snapshot + reports)"
	@echo "make backup-local          Refresh local snapshot + reports only (skip Letterboxd)"
	@echo "make backup-force          Force fresh Letterboxd scrape"
	@echo "make install-cron          Install daily backup cron (7am)"
	@echo "make uninstall-cron        Remove daily backup cron"
	@echo "make install-refresh-cron  Install */5 hook-poll cron (qBit→movienight refresh)"
	@echo "make uninstall-refresh-cron  Remove */5 hook-poll cron"
	@echo "make lint                  Run linter"
	@echo "make format                Format code"
	@echo "make check                 Fast local validators (delegates to homelab)"
	@echo "make clear-letterboxd-user-data  Clear cached watchlist/watched data for all users"

setup:
	uv sync
	@mkdir -p data/cache/letterboxd data/reports
	uv run setup-wizard

backup:
	./cron_backup.sh

# Refresh the local snapshot and reports without touching Letterboxd. Locked
# against cron_backup.sh so the daily 7 AM run can't race with this.
backup-local:
	flock -w 600 /tmp/movienight-refresh.lock sh -c 'uv run snapshot && uv run unwatched'

backup-force:
	uv run letterboxd --ratings --force
	uv run snapshot
	uv run unwatched

# Cron job marker - used to identify and update the cron entry
CRON_MARKER := mediabackup-$(shell pwd | md5sum | cut -c1-8)
CRON_SCHEDULE := 0 7 * * *
CRON_CMD := $(CURDIR)/cron_backup.sh >> $(CURDIR)/cron.log 2>&1

install-cron:
	@# Remove existing entry (if any) and add new one
	@( crontab -l 2>/dev/null | grep -v "$(CRON_MARKER)" ; \
	   echo "$(CRON_SCHEDULE) $(CRON_CMD) # $(CRON_MARKER)" ) | crontab -
	@echo "Cron job installed: $(CRON_SCHEDULE)"
	@echo "  $(CRON_CMD)"
	@echo ""
	@echo "View with: crontab -l | grep $(CRON_MARKER)"

uninstall-cron:
	@crontab -l 2>/dev/null | grep -v "$(CRON_MARKER)" | crontab - || true
	@echo "Cron job removed"

# Refresh-poll cron — picks up the qBit torrent-finished hook flag every 5 min
# and runs snapshot+unwatched. Idempotent (re-running this updates the entry).
REFRESH_CRON_MARKER := mediarefresh-$(shell pwd | md5sum | cut -c1-8)
REFRESH_CRON_SCHEDULE := */5 * * * *
REFRESH_CRON_CMD := $(CURDIR)/cron_refresh_on_flag.sh

install-refresh-cron:
	@( crontab -l 2>/dev/null | grep -v "$(REFRESH_CRON_MARKER)" ; \
	   echo "$(REFRESH_CRON_SCHEDULE) $(REFRESH_CRON_CMD) # $(REFRESH_CRON_MARKER)" ) | crontab -
	@echo "Refresh-poll cron installed: $(REFRESH_CRON_SCHEDULE)"
	@echo "  $(REFRESH_CRON_CMD)"
	@echo ""
	@echo "View with: crontab -l | grep $(REFRESH_CRON_MARKER)"

uninstall-refresh-cron:
	@crontab -l 2>/dev/null | grep -v "$(REFRESH_CRON_MARKER)" | crontab - || true
	@echo "Refresh-poll cron removed"

lint:
	uv run ruff check src/

format:
	uv run ruff format src/

clean:
	rm -rf .ruff_cache __pycache__ src/**/__pycache__

clear-letterboxd-user-data:
	rm -f data/cache/letterboxd/*.json
	@echo "Cleared watchlist/watched cache for all users"

# Publish — laptop-side. The actual service is a cron, so there's nothing
# to restart; the next 7 AM run (or `make backup` on mediaserver) picks up
# whatever was pulled. Standard verb across the homelab; see
# homelab/CLAUDE.md.
deploy:
	@echo "→ git push origin main"
	git push origin main
	@echo "→ ssh mediaserver: pull"
	ssh mediaserver "cd /home/joshlebed/code/movienight && git pull --rebase origin main"
	@echo "(no service restart — next 7 AM cron picks up the new code)"

# Fast local validators (compose / py / sh syntax). Delegates to homelab's
# check.sh — single source of truth across child repos.
check:
	@../homelab/scripts/check.sh "$(PWD)"
