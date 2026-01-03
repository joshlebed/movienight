#!/usr/bin/env python3
"""Interactive setup wizard for media-backup."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Optional: use requests if available for username validation
try:
    import requests

    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


def get_repo_root() -> Path:
    """Get the repository root directory."""
    return Path(__file__).parent.parent.parent


def prompt(message: str, default: str = "") -> str:
    """Prompt user for input with optional default."""
    if default:
        result = input(f"{message} [{default}]: ").strip()
        return result if result else default
    return input(f"{message}: ").strip()


def prompt_yes_no(message: str, default: bool = True) -> bool:
    """Prompt user for yes/no with default."""
    suffix = "[Y/n]" if default else "[y/N]"
    result = input(f"{message} {suffix}: ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes")


def prompt_choice(message: str, choices: list[str]) -> int:
    """Prompt user to select from numbered choices. Returns 0-based index."""
    print(message)
    for i, choice in enumerate(choices, 1):
        print(f"  {i}. {choice}")
    while True:
        result = input("Choice: ").strip()
        try:
            idx = int(result) - 1
            if 0 <= idx < len(choices):
                return idx
        except ValueError:
            pass
        print(f"  Please enter 1-{len(choices)}")


def prompt_list(message: str, example: str = "") -> list[str]:
    """Prompt user for comma-separated list."""
    hint = f" (e.g., {example})" if example else ""
    result = input(f"{message}{hint}: ").strip()
    if not result:
        return []
    return [item.strip() for item in result.split(",") if item.strip()]


def validate_path(path: str, must_exist: bool = True) -> tuple[bool, str]:
    """Validate a filesystem path."""
    if not path:
        return False, "Path cannot be empty"
    expanded = os.path.expanduser(path)
    if must_exist and not os.path.isdir(expanded):
        return False, f"Directory does not exist: {expanded}"
    return True, expanded


def prompt_path(message: str, must_exist: bool = True) -> str | None:
    """Prompt user for a path with validation."""
    while True:
        path = input(f"{message}: ").strip()
        if not path:
            return None
        valid, result = validate_path(path, must_exist)
        if valid:
            return result
        print(f"  Error: {result}")
        if not prompt_yes_no("  Try again?", default=True):
            return None


def run_git(args: list[str], cwd: Path) -> tuple[bool, str]:
    """Run a git command and return success status and output."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return True, result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return False, e.stderr.strip()
    except FileNotFoundError:
        return False, "git not found in PATH"


def check_prerequisites() -> list[str]:
    """Check for required tools. Returns list of missing tools."""
    missing = []

    # Check git
    if not shutil.which("git"):
        missing.append("git")

    # Check uv
    if not shutil.which("uv"):
        missing.append("uv (https://docs.astral.sh/uv/)")

    return missing


def validate_letterboxd_user(username: str) -> bool:
    """Check if a Letterboxd username exists."""
    if not HAS_REQUESTS:
        return True  # Skip validation if requests not available

    try:
        resp = requests.head(
            f"https://letterboxd.com/{username}/",
            timeout=5,
            allow_redirects=True,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return True  # Assume valid on network error


def setup_data_repo(data_dir: Path, remote_url: str) -> bool:
    """Initialize git repo in data/ and add remote."""
    # Check if already a git repo
    if (data_dir / ".git").exists():
        print("  Data directory is already a git repo")
        # Check if remote exists
        success, output = run_git(["remote", "get-url", "origin"], data_dir)
        if success:
            print(f"  Remote already configured: {output}")
            return True
        # Add remote if not configured
        success, error = run_git(["remote", "add", "origin", remote_url], data_dir)
        if not success:
            print(f"  Failed to add remote: {error}")
            return False
        print(f"  Added remote: {remote_url}")
        return True

    # Initialize new repo
    success, error = run_git(["init"], data_dir)
    if not success:
        print(f"  Failed to initialize git repo: {error}")
        return False
    print("  Initialized git repository")

    # Add remote
    success, error = run_git(["remote", "add", "origin", remote_url], data_dir)
    if not success:
        print(f"  Failed to add remote: {error}")
        return False
    print(f"  Added remote: {remote_url}")

    return True


def clone_existing_repo(data_dir: Path, remote_url: str) -> bool:
    """Clone an existing backup repo into data/."""
    # Remove empty data dir if it exists
    if data_dir.exists():
        if any(data_dir.iterdir()):
            print(f"  Error: {data_dir} is not empty")
            return False
        data_dir.rmdir()

    # Clone
    try:
        subprocess.run(
            ["git", "clone", remote_url, str(data_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"  Cloned {remote_url}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  Failed to clone: {e.stderr}")
        return False


def run_first_backup() -> bool:
    """Run the first backup."""
    repo_root = get_repo_root()
    try:
        subprocess.run(
            ["make", "backup"],
            cwd=repo_root,
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def fresh_setup(repo_root: Path, data_dir: Path, config_path: Path) -> int:
    """Run fresh setup flow."""
    # Create data directory structure
    data_dir.mkdir(exist_ok=True)
    (data_dir / "cache" / "letterboxd").mkdir(parents=True, exist_ok=True)
    (data_dir / "reports").mkdir(exist_ok=True)

    # Step 1: Letterboxd users
    print()
    print("Step 1: Letterboxd Users")
    print("-" * 30)
    print("Enter Letterboxd usernames to track.")

    users = []
    while not users:
        users = prompt_list("Usernames", example="alice, bob")
        if not users:
            print("  At least one username is required.")

    # Validate usernames
    print("  Validating usernames...", end="", flush=True)
    invalid = []
    for user in users:
        if not validate_letterboxd_user(user):
            invalid.append(user)
    if invalid:
        print()
        print(f"  Warning: Could not find: {', '.join(invalid)}")
        print("  (They may be private or you may have a typo)")
        if not prompt_yes_no("  Continue anyway?", default=True):
            return 1
    else:
        print(" OK")

    print(f"  Users: {', '.join(users)}")

    # Step 2: Media directories
    print()
    print("Step 2: Media Directories")
    print("-" * 30)
    print("Enter paths to your local media. Press Enter to skip.")
    print()

    media_dirs = {}

    movies_path = prompt_path("Movies directory")
    if movies_path:
        media_dirs["movies"] = movies_path

    tv_path = prompt_path("TV shows directory")
    if tv_path:
        media_dirs["tv"] = tv_path

    if prompt_yes_no("Add torrent directory? (for magnet links)", default=False):
        torrents_path = prompt_path("Torrents directory")
        if torrents_path:
            media_dirs["torrents"] = torrents_path

    if not media_dirs:
        print()
        print("  No directories configured. You can add them later in data/config.json")

    # Build and write config
    config = {
        "letterboxd_users": users,
        "media_directories": media_dirs,
    }

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    print()
    print(f"  Saved config to {config_path.relative_to(repo_root)}")

    # Step 3: Private backup repo
    print()
    print("Step 3: Version Control (optional)")
    print("-" * 30)
    print("Back up your data to a private git repo?")
    print()

    if prompt_yes_no("Set up backup repo?", default=True):
        print()
        print("Create a private repo on GitHub first, then enter the URL.")
        remote_url = prompt("Remote URL", "git@github.com:user/media-backup-data.git")

        if remote_url:
            print()
            if setup_data_repo(data_dir, remote_url):
                print("  Backup repo configured!")
            else:
                print("  Setup failed. You can configure manually later.")

    return 0


def restore_setup(data_dir: Path) -> int:
    """Restore from existing backup repo."""
    print()
    print("Restore from Existing Backup")
    print("-" * 30)
    print("Enter the URL of your existing backup repo.")
    print()

    remote_url = prompt("Remote URL")
    if not remote_url:
        print("  Cancelled.")
        return 1

    print()
    if clone_existing_repo(data_dir, remote_url):
        print("  Restore complete!")

        # Check if config exists
        config_path = data_dir / "config.json"
        if config_path.exists():
            print(f"  Found existing config at {config_path}")
        else:
            print("  Warning: No config.json found. You may need to create one.")

        return 0
    else:
        return 1


def main():
    """Run the interactive setup wizard."""
    repo_root = get_repo_root()
    data_dir = repo_root / "data"
    config_path = data_dir / "config.json"

    print()
    print("=" * 50)
    print("  Media Library Backup - Setup")
    print("=" * 50)

    # Check prerequisites
    missing = check_prerequisites()
    if missing:
        print()
        print("Missing required tools:")
        for tool in missing:
            print(f"  - {tool}")
        print()
        print("Please install these and try again.")
        return 1

    # Check if already set up
    if config_path.exists():
        print()
        print(f"Existing setup found: {config_path}")
        choice = prompt_choice(
            "What would you like to do?",
            ["Keep existing setup (exit)", "Start fresh (overwrite)", "Restore from backup repo"],
        )
        if choice == 0:
            print()
            print("Setup cancelled. Run 'make backup' to use existing config.")
            return 0
        elif choice == 2:
            # Remove existing data dir for restore
            print()
            print("Warning: This will replace your local data/ directory.")
            if not prompt_yes_no("Continue?", default=False):
                return 1
            shutil.rmtree(data_dir)
            result = restore_setup(data_dir)
            if result != 0:
                return result
            # Fall through to offer backup
        else:
            result = fresh_setup(repo_root, data_dir, config_path)
            if result != 0:
                return result
    else:
        # First time setup
        print()
        choice = prompt_choice(
            "Choose setup type:",
            ["Fresh setup (new user)", "Restore from existing backup repo"],
        )

        if choice == 0:
            result = fresh_setup(repo_root, data_dir, config_path)
            if result != 0:
                return result
        else:
            data_dir.mkdir(exist_ok=True)
            result = restore_setup(data_dir)
            if result != 0:
                return result

    # Offer to run first backup
    print()
    print("=" * 50)
    print("  Setup Complete!")
    print("=" * 50)
    print()

    if prompt_yes_no("Run first backup now?", default=True):
        print()
        if run_first_backup():
            print()
            print("Backup complete! Check data/reports/ for your results.")
        else:
            print()
            print("Backup failed. Try running 'make backup' manually.")
    else:
        print()
        print("Run 'make backup' when ready.")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
