from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.database import Database
from app.importer import import_file, parse_csv


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = Database(self.root / "test.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_table_csv_is_idempotent_and_updates_appended_rows(self):
        path = self.root / "live.csv"
        day = datetime.now().strftime("%Y-%m-%d")
        path.write_text(
            "Timestamp,Scenario,Score,Accuracy,Duration\n"
            f"{day} 10:00:00,Tile Frenzy,100,80%,60\n"
            f"{day} 10:02:00,Tile Frenzy,110,82%,60\n",
            encoding="utf-8",
        )
        first = import_file(self.database, path)
        second = import_file(self.database, path)
        self.assertEqual(first.inserted, 2)
        self.assertEqual(second.unchanged, 2)

        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{day} 10:04:00,Tile Frenzy,120,84%,60\n")
        third = import_file(self.database, path)
        self.assertEqual(third.inserted, 1)
        self.assertEqual(self.database.summary(7)["runs"], 3)

    def test_key_value_export_becomes_one_run(self):
        filename_day = datetime.now().strftime("%Y.%m.%d")
        path = self.root / f"Air - Challenge - {filename_day}-12.30.00 Stats.csv"
        path.write_text("Scenario:,Air\nScore:,8123\nAccuracy:,44.5%\nDuration:,120\n", encoding="utf-8")
        mode, records = parse_csv(path)
        result = import_file(self.database, path)
        run = self.database.recent_runs(1)[0]
        self.assertEqual(mode, "key_value")
        self.assertEqual(len(records), 1)
        self.assertEqual(result.inserted, 1)
        self.assertEqual(run["scenario"], "Air")
        self.assertEqual(run["score"], 8123.0)
        self.assertEqual(run["accuracy"], 44.5)

    def test_real_kovaaks_stats_export_keeps_run_and_setup_context(self):
        filename_day = datetime.now().strftime("%Y.%m.%d")
        path = self.root / f"Leaptrack Goated 75% Slightly Larger - Challenge - {filename_day}-17.17.41 Stats.csv"
        path.write_text(
            "Kill #,Timestamp,Bot,Weapon,TTK,Shots,Hits,Accuracy,Damage Done,Damage Possible,Efficiency,Cheated,OverShots\n"
            "\n"
            "Weapon,Shots,Hits,Damage Done,Damage Possible,,Sens Scale,Horiz Sens,Vert Sens,FOV,Hide Gun,Crosshair,Crosshair Scale,Crosshair Color,ADS Sens,ADS Zoom Scale,Avg Target Scale,Avg Time Dilation\n"
            "test1,4490,2470,2470.0,4490.0,\n"
            "\n"
            "Hit Count:,2470\n"
            "Miss Count:,2020\n"
            "Score:,2470.0\n"
            "Scenario:,Leaptrack Goated 75% Slightly Larger\n"
            "Challenge Start:,17:16:41.514\n"
            "Sens Scale:,cm/360\n"
            "Horiz Sens:,35.0\n"
            "DPI:,1600\n"
            "FOV:,105.0\n"
            "FOVScale:,Quake/Source\n"
            "Resolution:,2560x1440\n"
            "Avg FPS:,544.780701\n",
            encoding="utf-8",
        )

        mode, records = parse_csv(path)
        result = import_file(self.database, path)
        run = self.database.recent_runs(1)[0]
        summary = self.database.summary(7)

        self.assertEqual(mode, "kovaaks_stats")
        self.assertEqual(len(records), 1)
        self.assertEqual(result.inserted, 1)
        self.assertEqual(run["weapon"], "test1")
        self.assertEqual(run["shots"], 4490.0)
        self.assertEqual(run["hits"], 2470.0)
        self.assertEqual(run["misses"], 2020.0)
        self.assertAlmostEqual(run["accuracy"], 55.0111, places=3)
        self.assertAlmostEqual(run["duration_seconds"], 59.486, places=3)
        self.assertEqual(run["sensitivity"], 35.0)
        self.assertEqual(run["sensitivity_scale"], "cm/360")
        self.assertEqual(run["dpi"], 1600.0)
        self.assertEqual(run["fov_scale"], "Quake/Source")
        self.assertAlmostEqual(run["avg_fps"], 544.780701)
        self.assertAlmostEqual(summary["average_accuracy"], 55.0111, places=3)
        self.assertEqual(summary["latest_run_details"]["resolution"], "2560x1440")


if __name__ == "__main__":
    unittest.main()
