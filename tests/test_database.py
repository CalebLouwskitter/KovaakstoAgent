from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database import Database


class DatabaseMigrationTests(unittest.TestCase):
    def test_existing_run_table_gains_native_kovaaks_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.db"
            legacy_connection = sqlite3.connect(path)
            legacy_connection.executescript(
                """
                CREATE TABLE runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_path TEXT NOT NULL,
                    source_row INTEGER NOT NULL,
                    row_hash TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    scenario TEXT,
                    score REAL,
                    accuracy REAL,
                    duration_seconds REAL,
                    raw_json TEXT NOT NULL,
                    UNIQUE(source_path, source_row)
                );
                """
            )
            legacy_connection.close()

            database = Database(path)
            migrated_connection = sqlite3.connect(path)
            columns = {row[1] for row in migrated_connection.execute("PRAGMA table_info(runs)").fetchall()}
            migrated_connection.close()

            self.assertTrue({"shots", "hits", "misses", "avg_fps", "sensitivity"}.issubset(columns))

    def test_report_round_trip_preserves_provider_and_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Database(Path(temp) / "reports.db")
            report_id = database.save_report(
                "seven_day_digest",
                "gemini",
                "gemini-3.7-flash",
                "### Summary\n\n- **Runs:** 189",
                {"runs": 189, "scenarios": 35},
            )

            report = database.latest_report("seven_day_digest")

            self.assertEqual(report["id"], report_id)
            self.assertEqual(report["provider"], "gemini")
            self.assertEqual(report["model"], "gemini-3.7-flash")
            self.assertEqual(report["evidence"]["runs"], 189)
            self.assertIsNone(database.latest_report("missing"))


if __name__ == "__main__":
    unittest.main()
