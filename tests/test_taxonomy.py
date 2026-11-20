from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.analytics import PerformanceAnalytics
from app.database import Database
from app.taxonomy import (
    CATALOG_BY_NAME_LOWER,
    SKILL_CATEGORIES,
    SKILL_CATEGORY_IDS,
    TaxonomyManager,
    VERIFIED_SCENARIO_CATALOG,
    classify_scenario,
)


class TaxonomyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "tax.db")
        self.taxonomy = TaxonomyManager(self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_all_nine_categories_and_catalog_integrity(self) -> None:
        self.assertEqual(len(SKILL_CATEGORIES), 9)
        expected = {
            "static_clicking",
            "dynamic_clicking",
            "precise_switching",
            "speed_switching",
            "smooth_tracking",
            "reactive_tracking",
            "evasive_tracking",
            "movement_reading",
            "benchmark",
        }
        self.assertEqual(set(SKILL_CATEGORY_IDS), expected)
        for entry in VERIFIED_SCENARIO_CATALOG:
            self.assertTrue(entry.categories)
            for c in entry.categories:
                self.assertIn(c, expected)
            if entry.easier_variant:
                self.assertIn(entry.easier_variant.lower(), CATALOG_BY_NAME_LOWER)
            if entry.harder_variant:
                self.assertIn(entry.harder_variant.lower(), CATALOG_BY_NAME_LOWER)
            self.assertNotIn("KovaaKs", entry.name)  # never store share codes as names

    def test_multi_tag_classification_and_manual_overrides(self) -> None:
        vt_pasu = classify_scenario("VT Pasu Novice S4")
        self.assertIn("dynamic_clicking", vt_pasu)
        self.assertIn("benchmark", vt_pasu)

        custom = classify_scenario("Viscose Reactive Tracking Benchmark")
        self.assertIn("benchmark", custom)
        self.assertIn("reactive_tracking", custom)

        # Manual override persists across re-opening database and resets cleanly
        updated = self.taxonomy.set_manual_tags("Custom Drill XYZ", ["static_clicking", "precise_switching"])
        self.assertEqual(updated["source"], "manual")
        self.assertEqual(updated["categories"], ["static_clicking", "precise_switching"])

        reopened = TaxonomyManager(Database(Path(self.temp.name) / "tax.db"))
        self.assertEqual(reopened.get_tags("Custom Drill XYZ")["categories"], ["static_clicking", "precise_switching"])

        reset = reopened.reset_tags("Custom Drill XYZ")
        self.assertEqual(reset["source"], "auto")

        with self.assertRaises(ValueError):
            reopened.set_manual_tags("Custom Drill XYZ", ["not_a_real_category"])
        with self.assertRaises(ValueError):
            reopened.set_manual_tags("Custom Drill XYZ", [])

    def test_category_aggregation_never_averages_raw_scores_across_scenarios(self) -> None:
        # Scenario A scores ~100, Scenario B scores ~10,000, both in smooth_tracking.
        # Normalized skill_index must remain bounded [0..100], never ~5050.
        with self.db.connect() as conn:
            conn.execute("INSERT INTO sources(path, size_bytes, mtime_ns, status) VALUES('s.csv', 1, 1, 'ready')")
            rows = [
                ("s.csv", 1, "h1", "2026-08-01 10:00:00", "Smoothbot Voltaic Easy", 100.0),
                ("s.csv", 2, "h2", "2026-08-02 10:00:00", "Smoothbot Voltaic Easy", 105.0),
                ("s.csv", 3, "h3", "2026-08-03 10:00:00", "Smoothbot Voltaic Easy", 110.0),
                ("s.csv", 4, "h4", "2026-08-01 10:10:00", "Ascended Tracking v3", 10000.0),
                ("s.csv", 5, "h5", "2026-08-02 10:10:00", "Ascended Tracking v3", 10200.0),
                ("s.csv", 6, "h6", "2026-08-03 10:10:00", "Ascended Tracking v3", 10400.0),
            ]
            for path, idx, rhash, ts, scen, score in rows:
                conn.execute(
                    "INSERT INTO runs(source_path, source_row, row_hash, observed_at, scenario, score, raw_json) VALUES(?,?,?,?,?,?, '{}')",
                    (path, idx, rhash, ts, scen, score),
                )

        analytics = PerformanceAnalytics(self.db, self.taxonomy)
        report = analytics.full_report(days=30, anchor="2026-08-04")
        smooth = next(c for c in report["category_summary"]["categories"] if c["id"] == "smooth_tracking")
        self.assertEqual(smooth["runs"], 6)
        self.assertLessEqual(smooth["skill_index"], 100.0)
        self.assertGreater(smooth["skill_index"], 70.0)


if __name__ == "__main__":
    unittest.main()
