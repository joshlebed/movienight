read @README.md for high level context on the repo.

# agent notes for movienight

Cross-cutting infra docs (network, hosts, dev workflow, safety rails) live in
the sibling [`homelab-infra`](https://github.com/joshlebed/homelab-infra) repo.
If `~/code/homelab-infra` (or `/home/joshlebed/code/homelab-infra` on
mediaserver) doesn't exist, clone it:

```bash
git clone git@github.com:joshlebed/homelab-infra.git ../homelab-infra
```

This project is also generally distributable — most of the README applies to
anyone running it. Homelab-specific deployment notes are below.

## deployed on

`mediaserver`. The Letterboxd scrape + library scan + report generation runs as
a **daily cron job at 7 AM**, installed via `make install-cron`. Reports land in
`data/reports/` and are read by humans (no downstream automation depends on
them).

## data directory

`data/` is gitignored on purpose — it's intended to be its own private git repo
for backup/version-history purposes. On mediaserver, check whether `data/.git`
exists and what its remote is before assuming a fresh setup. Do not commit
anything inside `data/` to this repo.

## key gotchas

1. **Library paths are mediaserver-specific.** `data/config.json` references
   `media_directories` paths that are valid on mediaserver only. Don't propagate
   those paths into code or docs — they're intentionally per-deployment.

2. **Letterboxd scraper has a 6-month cache** by default (`make backup`). Use
   `make backup-force` to bypass during development or after upstream Letterboxd
   changes.

3. **The cron job depends on `make` and `uv` being on the cron PATH.** If the
   daily run starts failing silently, check `crontab -l` and confirm `uv`
   resolves under `/bin/sh`.

4. **No production-critical impact** if this fails — reports just get stale.
   Lower-stakes than the Pi/HA/QLC services.

## development

- `make setup` — interactive wizard (config + optional data-repo init)
- `make backup` — full backup, uses cache
- `make backup-force` — bypass cache
- `make install-cron` / `make uninstall-cron` — manage the cron entry
  (idempotent)
- `make install-dev` / `make lint` / `make format` — dev tooling
