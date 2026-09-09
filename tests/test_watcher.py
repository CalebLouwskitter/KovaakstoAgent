from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.watcher import CsvWatcher


class WatcherTests(unittest.TestCase):
    def test_successful_settings_read_clears_stale_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            watcher = CsvWatcher(database, lambda: "")
            watcher.last_error = "transient database error"

            self.assertEqual(watcher.scan_once(), [])
            self.assertIsNone(watcher.last_error)


if __name__ == "__main__":
    unittest.main()
