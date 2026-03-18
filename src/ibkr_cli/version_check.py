from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.request import urlopen
from urllib.error import URLError

from platformdirs import user_cache_dir

PACKAGE_NAME = "ibkr-cli"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
CACHE_DIR = Path(user_cache_dir("ibkr-cli", "ibkr"))
CACHE_FILE = CACHE_DIR / "latest_version.json"
CHECK_INTERVAL = 86400  # 24 hours


def fetch_latest_version(timeout: int = 3) -> Optional[str]:
    """Fetch the latest version from PyPI. Returns None on any failure."""
    try:
        with urlopen(PYPI_URL, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return data["info"]["version"]
    except (URLError, OSError, json.JSONDecodeError, KeyError):
        return None


def get_cached_latest_version() -> Optional[str]:
    """Return cached latest version if the cache is fresh, otherwise fetch and cache."""
    try:
        if CACHE_FILE.exists():
            cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - cache.get("timestamp", 0) < CHECK_INTERVAL:
                return cache.get("version")
    except (json.JSONDecodeError, OSError):
        pass

    latest = fetch_latest_version()
    if latest:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(
                json.dumps({"version": latest, "timestamp": time.time()}),
                encoding="utf-8",
            )
        except OSError:
            pass
    return latest


def _parse_version(v: str) -> tuple:
    """Parse a version string like '0.1.1' into a comparable tuple."""
    parts = []
    for part in v.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(part)
    return tuple(parts)


def check_for_update(current_version: str) -> Optional[str]:
    """Check if a newer version is available. Returns the latest version string if newer, None otherwise."""
    latest = get_cached_latest_version()
    if latest and _parse_version(latest) > _parse_version(current_version):
        return latest
    return None


def detect_installer() -> str:
    """Detect whether ibkr-cli was installed via pipx or pip."""
    if shutil.which("pipx"):
        result = subprocess.run(
            ["pipx", "list", "--short"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and "ibkr-cli" in result.stdout:
            return "pipx"
    return "pip"


def run_update() -> tuple[bool, str]:
    """Run the update command. Returns (success, message)."""
    installer = detect_installer()
    if installer == "pipx":
        cmd = ["pipx", "upgrade", PACKAGE_NAME]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE_NAME]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        # Clear the version cache so the next check picks up the new version
        try:
            CACHE_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return True, result.stdout.strip()
    else:
        return False, result.stderr.strip()
