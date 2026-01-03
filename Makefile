# Media Library Backup
#
# Quick start:
#   make setup    - Interactive setup wizard
#   make backup   - Run backup (scrapes Letterboxd, scans media, generates reports)
#
# The backup uses 6-month caching for Letterboxd data. Use `make backup-force` to bypass.

.PHONY: setup backup backup-force lint format clean help

help:
	@echo "make setup        Interactive setup wizard"
	@echo "make backup       Run backup (uses 6-month cache)"
	@echo "make backup-force Force fresh Letterboxd scrape"
	@echo "make lint         Run linter"
	@echo "make format       Format code"

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

lint:
	uv run ruff check src/

format:
	uv run ruff format src/

clean:
	rm -rf .ruff_cache __pycache__ src/**/__pycache__
