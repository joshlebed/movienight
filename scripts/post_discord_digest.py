#!/usr/bin/env python3
"""
Post a "what's new on the watchlists" digest to Discord.

Run from the movienight `data/` git repo, with two commit refs as args:
the previous run's commit and the current one. Diffs the per-user
watchlist JSONs between them and posts a single Discord embed listing
additions per user.

Reads the Discord webhook URL from media-stack's .env (same channel
Seerr's lifecycle webhook posts to).

Usage:
  post_discord_digest.py <prev_commit> <current_commit>
"""

import datetime as _dt
import glob
import json
import os
import subprocess
import sys
import urllib.request

DATA_DIR = os.environ.get("DATA_DIR", "/home/joshlebed/code/movienight/data")
ENV_PATH = os.environ.get(
    "MEDIA_STACK_ENV", "/home/joshlebed/code/media-stack/.env"
)


def read_webhook_url() -> str:
    if "DISCORD_WEBHOOK_URL" in os.environ:
        return os.environ["DISCORD_WEBHOOK_URL"]
    try:
        with open(ENV_PATH) as f:
            for line in f:
                if line.startswith("DISCORD_WEBHOOK_URL="):
                    return line.strip().split("=", 1)[1]
    except OSError:
        pass
    return ""


def git_show(commit: str, path: str) -> str | None:
    """Return file contents at commit, or None if not present at that ref."""
    try:
        return subprocess.check_output(
            ["git", "show", f"{commit}:{path}"],
            cwd=DATA_DIR,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except subprocess.CalledProcessError:
        return None


def load_watchlist(content: str | None) -> list[dict]:
    if not content:
        return []
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return []


def diff_watchlists(prev_commit: str, current_commit: str) -> dict[str, list[dict]]:
    """
    Returns {username: [film_dict, ...]} of newly-added films per user.

    The data repo's root is `data/` itself; inside, watchlist JSONs live at
    `cache/letterboxd/<user>_watchlist.json`. So paths passed to `git show`
    are relative to `data/`, not to the parent movienight repo.
    """
    repo_root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=DATA_DIR, text=True,
    ).strip()
    files = glob.glob(os.path.join(repo_root, "cache/letterboxd/*_watchlist.json"))
    additions: dict[str, list[dict]] = {}

    for f in files:
        rel = os.path.relpath(f, repo_root)  # cache/letterboxd/<user>_watchlist.json
        user = os.path.basename(rel).removesuffix("_watchlist.json")
        prev = load_watchlist(git_show(prev_commit, rel))
        cur = load_watchlist(git_show(current_commit, rel))
        prev_ids = {film.get("tmdb_id") or film.get("imdb_id") or film["title"]
                    for film in prev}
        new = [film for film in cur
               if (film.get("tmdb_id") or film.get("imdb_id") or film["title"])
               not in prev_ids]
        if new:
            additions[user] = new
    return additions


def format_embed(additions: dict[str, list[dict]]) -> dict:
    today = _dt.date.today().isoformat()
    lines = []
    total = 0
    for user, films in additions.items():
        if not films:
            continue
        # sort by Letterboxd rating descending (highest-rated first)
        films_sorted = sorted(
            films,
            key=lambda f: f.get("letterboxd_rating") or 0,
            reverse=True,
        )
        top = films_sorted[:5]  # cap per-user to keep embed compact
        lines.append(f"\n**{user}** added {len(films)}:")
        for f in top:
            title = f.get("title", "?")
            year = f.get("year")
            rating = f.get("letterboxd_rating")
            url = f.get("film_url")
            line = f"- "
            if url:
                line += f"[{title} ({year})]({url})" if year else f"[{title}]({url})"
            else:
                line += f"{title} ({year})" if year else title
            if rating:
                line += f" — ⭐ {rating}"
            lines.append(line)
        if len(films) > len(top):
            lines.append(f"  …and {len(films) - len(top)} more")
        total += len(films)

    if not lines:
        return {}

    desc = "\n".join(lines).lstrip("\n")
    if len(desc) > 4000:
        desc = desc[:3990] + "\n…"

    return {
        "title": f"📋 Watchlist additions — {today}",
        "description": desc,
        "color": 0x5865F2,  # blurple
        "footer": {
            "text": f"{total} new films across {len(additions)} watchlist(s) — "
                    f"see joshlebed/movienight-data on GitHub",
        },
    }


def post_to_discord(webhook_url: str, embed: dict) -> bool:
    # User-Agent header is required: Discord's Cloudflare layer 403s
    # `Python-urllib/...` (the stdlib default).
    payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "movienight-digest/1.0 (+https://github.com/joshlebed/movienight)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except Exception as e:
        print(f"discord post failed: {e}", file=sys.stderr)
        return False


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <prev_commit> <current_commit>",
              file=sys.stderr)
        sys.exit(2)
    prev_commit, current_commit = sys.argv[1], sys.argv[2]

    webhook_url = read_webhook_url()
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL not found, skipping digest", file=sys.stderr)
        return

    additions = diff_watchlists(prev_commit, current_commit)
    if not additions:
        print("no watchlist additions today, skipping post")
        return

    embed = format_embed(additions)
    if not embed:
        print("no embed content, skipping post")
        return

    ok = post_to_discord(webhook_url, embed)
    total = sum(len(v) for v in additions.values())
    print(f"posted digest: {total} additions across {len(additions)} users → "
          f"{'ok' if ok else 'FAILED'}")


if __name__ == "__main__":
    main()
