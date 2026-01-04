# Media Library Backup
#
# Quick start:
#   make setup    - Interactive setup wizard
#   make backup   - Run backup (scrapes Letterboxd, scans media, generates reports)
#
# The backup uses 6-month caching for Letterboxd data. Use `make backup-force` to bypass.

.PHONY: setup backup backup-force install-cron uninstall-cron lint format clean help

help:
	@echo "make setup         Interactive setup wizard"
	@echo "make backup        Run backup (uses 6-month cache)"
	@echo "make backup-force  Force fresh Letterboxd scrape"
	@echo "make install-cron  Install daily cron job (7am)"
	@echo "make uninstall-cron Remove cron job"
	@echo "make lint          Run linter"
	@echo "make format        Format code"

setup:
	uv sync
	@mkdir -p data/cache/letterboxd data/reports
	uv run setup-wizard

backup:
	./cron_backup.sh

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

lint:
	uv run ruff check src/

format:
	uv run ruff format src/

clean:
	rm -rf .ruff_cache __pycache__ src/**/__pycache__
