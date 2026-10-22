from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.service import AppService
from app.steam import STATS_SUBPATH, find_stats_folders, library_paths_from_vdf


VDF = r'''
"libraryfolders"
{
	"0"
	{
		"path"		"C:\\Program Files (x86)\\Steam"
		"label"		""
		"apps" { "228980" "1" }
	}
	"1"
	{
		"path"		"%s"
		"apps" { "824270" "1" }
	}
}
'''


@unittest.skipUnless(sys.platform == "win32", "Steam path detection")
class SteamDetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make_stats(self, library: Path, with_csv: bool = False) -> Path:
        stats = library / STATS_SUBPATH
        stats.mkdir(parents=True)
        if with_csv:
            (stats / "Run - Challenge - 2026.08.30-12.00.00 Stats.csv").write_text("Score:,1\n", encoding="utf-8")
        return stats

    def test_parses_escaped_library_paths(self):
        paths = library_paths_from_vdf(VDF % r"G:\\SteamLibrary")
        self.assertEqual(paths, [Path(r"C:\Program Files (x86)\Steam"), Path(r"G:\SteamLibrary")])

    def test_follows_libraryfolders_and_prefers_folders_with_runs(self):
        steam = self.base / "Steam"
        empty = self.make_stats(steam)
        library = self.base / "Games" / "SteamLibrary"
        played = self.make_stats(library, with_csv=True)
        (steam / "steamapps" / "libraryfolders.vdf").write_text(
            VDF % str(library).replace("\\", "\\\\"), encoding="utf-8")
        found = find_stats_folders([steam, self.base / "missing"])
        self.assertEqual(found, [played, empty])

    def test_duplicate_roots_are_reported_once(self):
        steam = self.base / "Steam"
        stats = self.make_stats(steam)
        self.assertEqual(find_stats_folders([steam, steam, Path(str(steam) + os.sep)]), [stats])

    def test_no_install_finds_nothing(self):
        self.assertEqual(find_stats_folders([self.base / "nowhere"]), [])


class AutodetectWatchPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = AppService(self.temp.name)
        patcher = patch.dict(os.environ, {"KOVAAKS_CSV_PATH": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_first_run_uses_detected_folder(self):
        found = Path(self.temp.name) / "stats"
        self.assertEqual(self.service.autodetect_watch_path(lambda: [found]), str(found))
        status = self.service.status()
        self.assertEqual(status["watch_path"], str(found))
        self.assertTrue(status["watch_path_autodetected"])

    def test_saved_or_cleared_path_is_never_replaced(self):
        self.service.database.set_setting("watch_path", "")
        self.assertIsNone(self.service.autodetect_watch_path(lambda: [Path("elsewhere")]))
        self.assertEqual(self.service.watch_path, "")

    def test_nothing_found_leaves_setting_unset(self):
        self.assertIsNone(self.service.autodetect_watch_path(lambda: []))
        self.assertEqual(self.service.database.get_setting("watch_path", "unset"), "unset")

    def test_manual_change_clears_detected_flag(self):
        found = Path(self.temp.name)
        self.service.autodetect_watch_path(lambda: [found / "stats"])
        self.service.update_settings(str(found), "")
        self.assertFalse(self.service.status()["watch_path_autodetected"])


if __name__ == "__main__":
    unittest.main()
