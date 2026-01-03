.PHONY: install install-fuzzy install-dev setup backup letterboxd letterboxd-force snapshot unwatched lint format clean help

# Default target
help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Setup:"
	@echo "  install        Install dependencies"
	@echo "  install-fuzzy  Install with faster fuzzy matching"
	@echo "  install-dev    Install with dev tools (ruff)"
	@echo "  setup          Create data/ directory with example config"
	@echo ""
	@echo "Backup operations:"
	@echo "  backup         Run full backup (letterboxd + snapshot + unwatched)"
	@echo "  letterboxd     Scrape Letterboxd (uses 24h cache)"
	@echo "  letterboxd-force  Force fresh Letterboxd scrape"
	@echo "  snapshot       Scan local media directories"
	@echo "  unwatched      Generate per-user and pairwise lists"
	@echo ""
	@echo "Development:"
	@echo "  lint           Run linter"
	@echo "  format         Format code"
	@echo "  clean          Remove cache files"

# Setup targets
install:
	uv sync

install-fuzzy:
	uv sync --extra fuzzy

install-dev:
	uv sync --extra dev

setup: install
	@mkdir -p data
	@if [ ! -f data/config.json ]; then \
		cp config.example.json data/config.json; \
		echo "Created data/config.json - edit with your settings"; \
	else \
		echo "data/config.json already exists"; \
	fi

# Backup operations
backup:
	./cron_backup.sh

letterboxd:
	uv run letterboxd

letterboxd-force:
	uv run letterboxd --force

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
