"""Background filesystem polling for automatic CSV ingestion.

Watches a single CSV file or a folder of per-run CSV exports.
Implements a two-phase 'stable-write' check to avoid importing partially written
files while the game is still flushing output to disk.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from .database import Database
from .importer import discover_csv_files, import_file


class CsvWatcher:
    """Polls for stable CSV changes in a background daemon thread."""

    def __init__(
        self,
        database: Database,
        get_watch_path: Callable[[], str],
        interval_seconds: float = 2.0,
    ):
        self.database = database
        self.get_watch_path = get_watch_path
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Maps resolved file path -> (size_bytes, mtime_ns)
        self._observed: dict[str, tuple[int, int]] = {}
        self.last_error: str | None = None
        self.last_import: dict | None = None

    @property
    def running(self) -> bool:
        """True if the background watcher thread is alive and active."""
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        """Start the background polling thread if not already running."""
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="csv-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the background thread to stop and wait for it to terminate."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval_seconds + 1)

    def scan_once(self) -> list[dict]:
        """Perform a single pass over discovered CSV files.

        Two-phase stable write check:
        1. On first sighting of a changed or new file, record its (size, mtime) signature.
        2. On the next cycle, verify that the signature has remained identical.
        3. Only import once the signature is stable, preventing partial reads while Kovaak's writes.
        """
        watch_path = self.get_watch_path().strip()
        # A successful settings read means any prior transient watcher error is stale.
        self.last_error = None
        if not watch_path:
            return []
        imported = []
        for path in discover_csv_files(watch_path):
            try:
                resolved = str(path.resolve())
                stat = path.stat()
                signature = (stat.st_size, stat.st_mtime_ns)
                previous_observation = self._observed.get(resolved)
                stored = self.database.source_signature(resolved)
                self._observed[resolved] = signature

                # Skip if database already imported this exact size and mtime
                if stored == signature:
                    continue

                # Stable-write check: file must be observed twice with identical signature
                if previous_observation != signature:
                    continue

                result = import_file(self.database, path).to_dict()
                imported.append(result)
                self.last_import = result
                self.last_error = None
            except (OSError, ValueError) as exc:
                self.last_error = str(exc)
        return imported

    def _run(self) -> None:
        """Background loop executing scan_once at configured intervals until stopped."""
        while not self._stop.wait(self.interval_seconds):
            try:
                self.scan_once()
            except Exception as exc:  # watcher must stay alive after a bad file
                self.last_error = str(exc)
