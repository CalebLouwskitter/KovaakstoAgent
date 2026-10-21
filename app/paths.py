"""Filesystem locations for bundled resources and per-user data.

User data lives outside the program folder so installs, updates and source
checkouts never hold the player's database:

- Windows: %APPDATA%\\Kovaak Agent
- Other systems: $XDG_DATA_HOME/kovaak-agent (default ~/.local/share/kovaak-agent)

KOVAAK_AGENT_DATA_DIR overrides the location. Version 0.1.0 kept data in the
project's data/ folder; migrate_legacy_data copies it forward once.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


APP_NAME = "Kovaak Agent"
DATABASE_NAME = "kovaaks-agent.db"
DATA_DIR_ENVIRONMENT = "KOVAAK_AGENT_DATA_DIR"


def resource_root() -> Path:
    """Folder holding bundled read-only files (web/, samples/, packaging/)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def user_data_dir() -> Path:
    """Per-user writable folder for the database and other private state."""
    override = os.environ.get(DATA_DIR_ENVIRONMENT, "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "kovaak-agent"


def legacy_data_dir() -> Path:
    """The 0.1.0 location: data/ inside the source checkout."""
    return Path(__file__).resolve().parents[1] / "data"


def migrate_legacy_data(legacy_dir: Path, data_dir: Path) -> Path | None:
    """Copy a 0.1.0 database into data_dir if data_dir has none yet.

    Uses SQLite's backup API so committed write-ahead-log data comes along and
    the copy is consistent. The legacy file is left in place as a safety copy.
    Returns the migrated source path, or None when nothing was copied.
    """
    source = legacy_dir / DATABASE_NAME
    target = data_dir / DATABASE_NAME
    if target.exists() or not source.is_file():
        return None
    try:
        if source.resolve() == target.resolve():
            return None
    except OSError:
        return None
    data_dir.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".migrating")
    partial.unlink(missing_ok=True)
    origin = sqlite3.connect(source)
    try:
        destination = sqlite3.connect(partial)
        try:
            origin.backup(destination)
        finally:
            destination.close()
    finally:
        origin.close()
    partial.replace(target)
    return source
