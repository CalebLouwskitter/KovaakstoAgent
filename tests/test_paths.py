from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import Database
from app.paths import DATA_DIR_ENVIRONMENT, migrate_legacy_data, user_data_dir
from app.server import create_server


class UserDataDirTests(unittest.TestCase):
    def test_environment_override_wins(self):
        with patch.dict(os.environ, {DATA_DIR_ENVIRONMENT: "~/kovaak-test-data"}):
            self.assertEqual(user_data_dir(), Path("~/kovaak-test-data").expanduser())

    @unittest.skipUnless(sys.platform == "win32", "Windows location")
    def test_windows_uses_roaming_appdata(self):
        environment = {key: value for key, value in os.environ.items() if key != DATA_DIR_ENVIRONMENT}
        environment["APPDATA"] = r"C:\Users\Player\AppData\Roaming"
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(user_data_dir(), Path(r"C:\Users\Player\AppData\Roaming\Kovaak Agent"))


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.legacy = Path(self.temp.name) / "project" / "data"
        self.target = Path(self.temp.name) / "appdata" / "Kovaak Agent"

    def tearDown(self):
        self.temp.cleanup()

    def test_copies_database_including_uncheckpointed_wal(self):
        database = Database(self.legacy / "kovaaks-agent.db")
        database.set_setting("watch_path", r"D:\stats")
        # Keep a connection open with autocheckpoint off so the setting lives only in the WAL.
        holder = sqlite3.connect(self.legacy / "kovaaks-agent.db")
        holder.execute("PRAGMA wal_autocheckpoint = 0")
        holder.execute("INSERT INTO settings(key, value) VALUES('wal_only', 'yes')")
        holder.commit()
        try:
            self.assertTrue((self.legacy / "kovaaks-agent.db-wal").exists())
            migrated = migrate_legacy_data(self.legacy, self.target)
        finally:
            holder.close()
        self.assertEqual(migrated, self.legacy / "kovaaks-agent.db")
        copied = Database(self.target / "kovaaks-agent.db")
        self.assertEqual(copied.get_setting("watch_path"), r"D:\stats")
        self.assertEqual(copied.get_setting("wal_only"), "yes")
        self.assertTrue((self.legacy / "kovaaks-agent.db").exists(), "legacy copy is kept")

    def test_never_overwrites_existing_user_data(self):
        Database(self.legacy / "kovaaks-agent.db").set_setting("watch_path", "old")
        Database(self.target / "kovaaks-agent.db").set_setting("watch_path", "new")
        self.assertIsNone(migrate_legacy_data(self.legacy, self.target))
        self.assertEqual(Database(self.target / "kovaaks-agent.db").get_setting("watch_path"), "new")

    def test_nothing_to_migrate(self):
        self.assertIsNone(migrate_legacy_data(self.legacy, self.target))
        self.assertFalse(self.target.exists())

    def test_create_server_migrates_and_uses_data_dir(self):
        Database(self.legacy / "kovaaks-agent.db").set_setting("watch_path", "")
        with patch("app.server.legacy_data_dir", return_value=self.legacy), \
                patch("app.server.print"):
            server, service = create_server("127.0.0.1", 0, self.target)
        try:
            self.assertEqual(service.data_dir, self.target.resolve())
            self.assertTrue((self.target / "kovaaks-agent.db").exists())
            # The explicitly empty legacy watch path is respected, so detection is skipped.
            self.assertEqual(service.watch_path, "")
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
