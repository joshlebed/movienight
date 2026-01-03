.PHONY: install install-fuzzy install-dev setup setup-quick backup backup-with-ratings letterboxd letterboxd-force letterboxd-ratings snapshot unwatched lint format clean migrate help

# Default target
help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Setup:"
	@echo "  install        Install dependencies"
	@echo "  install-fuzzy  Install with faster fuzzy matching"
	@echo "  install-dev    Install with dev tools (ruff)"
	@echo "  setup          Interactive setup wizard (recommended)"
	@echo "  setup-quick    Non-interactive setup (copies example config)"
	@echo ""
	@echo "Backup operations:"
	@echo "  backup              Run full backup (no ratings)"
	@echo "  backup-with-ratings Run full backup with ratings (slower)"
	@echo "  letterboxd          Scrape Letterboxd (uses 24h cache)"
	@echo "  letterboxd-force    Force fresh Letterboxd scrape"
	@echo "  letterboxd-ratings  Scrape + fetch ratings"
	@echo "  snapshot            Scan local media directories"
	@echo "  unwatched           Generate per-user and pairwise lists"
	@echo ""
	@echo "Development:"
	@echo "  lint           Run linter"
	@echo "  format         Format code"
	@echo "  clean          Remove cache files"
	@echo "  migrate        Move old data files to new structure"

# Setup targets
install:
	uv sync

install-fuzzy:
	uv sync --extra fuzzy

install-dev:
	uv sync --extra dev

setup: install
	@mkdir -p data/cache/letterboxd data/reports
	uv run setup-wizard

setup-quick: install
	@mkdir -p data/cache/letterboxd data/reports
	@if [ ! -f data/config.json ]; then \
		cp config.example.json data/config.json; \
		echo "Created data/config.json - edit with your settings"; \
	else \
		echo "data/config.json already exists"; \
	fi

# Backup operations
backup:
	./cron_backup.sh

backup-with-ratings:
	uv run letterboxd --ratings
	uv run snapshot
	uv run unwatched

letterboxd:
	uv run letterboxd

letterboxd-force:
	uv run letterboxd --force

letterboxd-ratings:
	uv run letterboxd --ratings

snapshot:
	uv run snapshot

unwatched:
	uv run unwatched

# Development
lint:
	uv run ruff check src/

format:
	uv run ruff format src/

clean:
	rm -rf .ruff_cache __pycache__ src/**/__pycache__

# Migration helper - moves old flat data files to new structure
migrate:
	@mkdir -p data/cache/letterboxd data/reports
	@# Move letterboxd cache files
	@for f in data/*_watched.json data/*_watchlist.json; do \
		[ -f "$$f" ] && mv "$$f" data/cache/letterboxd/ 2>/dev/null || true; \
	done
	@# Move media library and ratings cache
	@[ -f data/media_library.json ] && mv data/media_library.json data/cache/ 2>/dev/null || true
	@[ -f data/ratings_cache.json ] && mv data/ratings_cache.json data/cache/ 2>/dev/null || true
	@# Remove old txt files (will be regenerated as markdown)
	@rm -f data/*_watchlist_available.txt data/*_watchlist_missing.txt data/*_undiscovered.txt
	@rm -f data/*_shared_watchlist_*.txt data/media_list.txt
	@echo "Migration complete. Run 'make unwatched' to regenerate reports."
