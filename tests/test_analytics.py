from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.analytics import EPISTEMIC_CAVEAT, PerformanceAnalytics
from app.database import Database
from app.importer import import_file
from app.taxonomy import TaxonomyManager


class AnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "analytics.db")
        self.taxonomy = TaxonomyManager(self.db)
        self.analytics = PerformanceAnalytics(self.db, self.taxonomy)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _import_csv_rows(self, filename: str, header: str, rows: list[str]) -> None:
        path = self.root / filename
        path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
        import_file(self.db, path)

    def test_empty_single_constant_and_missing_stats_edge_cases(self) -> None:
        # Empty DB
        empty = self.analytics.full_report()
        self.assertTrue(empty["empty"])
        self.assertEqual(empty["total_runs"], 0)

        # Single run with missing accuracy, shots, duration
        self._import_csv_rows(
            "single.csv",
            "Timestamp,Scenario,Score",
            ["2026-08-01 10:00:00,1wall6targets small,500.0"],
        )
        single = self.analytics.full_report(anchor="2026-08-01")
        self.assertFalse(single["empty"])
        self.assertEqual(single["total_runs"], 1)
        self.assertIsNone(single["scenarios"][0]["weighted_accuracy"])
        self.assertIsNone(single["scenarios"][0]["score"]["cv_pct"])
        self.assertTrue(any(d["pattern_code"] == "sparse_data_coverage" for d in single["diagnoses"]))

        # Constant scores across 5 runs (zero std_dev / zero CV)
        self._import_csv_rows(
            "constant.csv",
            "Timestamp,Scenario,Score",
            [
                "2026-08-02 10:00:00,Constant Drill,250.0",
                "2026-08-03 10:00:00,Constant Drill,250.0",
                "2026-08-04 10:00:00,Constant Drill,250.0",
                "2026-08-05 10:00:00,Constant Drill,250.0",
                "2026-08-06 10:00:00,Constant Drill,250.0",
            ],
        )
        const_report = self.analytics.full_report(anchor="2026-08-06")
        const_s = next(s for s in const_report["scenarios"] if s["scenario"] == "Constant Drill")
        self.assertEqual(const_s["score"]["cv_pct"], 0.0)
        self.assertEqual(const_s["consistency_score"], 100.0)

    def test_speed_over_precision_tradeoff_plateau_and_epistemic_caveats(self) -> None:
        # Speed-over-precision in 1wall6targets small: scores climb slightly while accuracy falls and CV widens
        rows = []
        base = datetime(2026, 7, 1, 18, 0, 0)
        static_data = [
            (840, 92.0), (845, 91.5), (850, 91.0), (855, 90.5), (860, 90.0), (865, 89.5),
            (850, 84.0), (920, 82.0), (860, 83.5), (940, 81.0), (855, 82.5), (950, 80.5),
        ]
        for i, (sc, acc) in enumerate(static_data):
            ts = (base + timedelta(days=i * 2)).strftime("%Y-%m-%d %H:%M:%S")
            shots = 150
            hits = round(shots * acc / 100.0)
            rows.append(f"{ts},1wall6targets small,{sc},{acc}%,60,{shots},{hits},{shots - hits}")

        # Plateau in Smoothbot Voltaic Easy: early PB then 11 flat lower runs over 22 days
        smooth_scores = [3150, 2980, 3005, 2990, 3010, 2995, 3000, 2985, 3005, 2990, 3000, 2995]
        for i, sc in enumerate(smooth_scores):
            ts = (base + timedelta(days=i * 2, minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
            rows.append(f"{ts},Smoothbot Voltaic Easy,{sc},46.0%,60,1200,552,648")

        self._import_csv_rows("patterns.csv", "Timestamp,Scenario,Score,Accuracy,Duration,Shots,Hits,Misses", rows)
        report = self.analytics.full_report(days=90, anchor="2026-07-30")

        static_s = next(s for s in report["scenarios"] if s["scenario"] == "1wall6targets small")
        self.assertEqual(static_s["accuracy_speed_tradeoff"]["state"], "speed_over_precision")

        smooth_s = next(s for s in report["scenarios"] if s["scenario"] == "Smoothbot Voltaic Easy")
        self.assertTrue(smooth_s["plateau"]["is_plateau"])

        codes = {d["pattern_code"] for d in report["diagnoses"]}
        self.assertIn("speed_over_precision_tradeoff", codes)
        self.assertIn("training_plateau", codes)

        for diag in report["diagnoses"]:
            self.assertIn("sample_size", diag)
            self.assertIn("confidence", diag)
            self.assertTrue(diag["alternative_explanations"])
            self.assertEqual(diag["epistemic_note"], EPISTEMIC_CAVEAT)

    def test_within_session_fatigue_hardware_change_mixed_duration_and_variants(self) -> None:
        rows = []
        # Create 4 multi-run sessions where late-session runs drop significantly
        for day in range(4):
            d_str = (datetime(2026, 8, 1) + timedelta(days=day * 2)).strftime("%Y-%m-%d")
            sens = 35.0 if day < 2 else 45.0
            dpi = 800 if day < 2 else 1600
            factor = 1.0 if day < 2 else 0.90  # 10% dip after sensitivity change
            # 6 runs per session: early 100, mid 106, late 90
            session_scores = [100, 102, 108, 107, 91, 89]
            for j, sc in enumerate(session_scores):
                ts = f"{d_str} 18:{j * 4:02d}:00"
                dur = 120 if (day == 0 and j == 0) else 60  # mixed duration check
                rows.append(f"{ts},Pasu Voltaic Easy,{sc * factor:.1f},78%,{dur},100,78,22,{sens},cm/360,{dpi}")
        # Add sibling scenario variant
        rows.append("2026-08-09 18:00:00,Pasu Voltaic,500.0,75%,60,100,75,25,45.0,cm/360,1600")

        self._import_csv_rows(
            "session_hw.csv",
            "Timestamp,Scenario,Score,Accuracy,Duration,Shots,Hits,Misses,Horiz Sens,Sens Scale,DPI",
            rows,
        )
        report = self.analytics.full_report(days=30, anchor="2026-08-10")
        self.assertEqual(report["session_progression"]["pattern"], "late_session_drop")
        self.assertLess(report["session_progression"]["avg_late_drift_pct"], -2.0)

        self.assertTrue(report["hardware_events"])
        hw = report["hardware_events"][0]
        self.assertTrue(hw["impact"]["comparable"])
        self.assertLess(hw["impact"]["index_delta_pct"], -5.0)

        pasu_easy = next(s for s in report["scenarios"] if s["scenario"] == "Pasu Voltaic Easy")
        self.assertTrue(pasu_easy["duration"]["mixed_durations"])
        self.assertIn("Pasu Voltaic", pasu_easy["variant_siblings"])

        codes = {d["pattern_code"] for d in report["diagnoses"]}
        self.assertIn("late_session_fatigue_drop", codes)
        self.assertIn("post_hardware_adaptation", codes)
        self.assertIn("inconsistent_run_duration", codes)

    def test_large_dataset_2500_runs_performance_and_rolling_windows(self) -> None:
        base = datetime(2026, 1, 1, 12, 0, 0)
        scenarios = ["1wall6targets small", "Smoothbot Voltaic Easy", "Pasu Voltaic Easy", "voxTargetSwitch Click"]
        with self.db.connect() as conn:
            conn.execute("INSERT INTO sources(path, size_bytes, mtime_ns, status) VALUES('big.csv', 100, 100, 'ready')")
            batch = []
            for i in range(2500):
                ts = (base + timedelta(hours=i * 2)).strftime("%Y-%m-%d %H:%M:%S")
                scen = scenarios[i % len(scenarios)]
                sc = 100.0 + (i % 50) + (i * 0.01)
                batch.append(("big.csv", i + 1, f"h_{i}", ts, scen, sc, 80.0, 60.0))
            conn.executemany(
                "INSERT INTO runs(source_path, source_row, row_hash, observed_at, scenario, score, accuracy, duration_seconds, raw_json) VALUES(?,?,?,?,?,?,?,?, '{}')",
                batch,
            )

        t0 = time.perf_counter()
        report = self.analytics.full_report(days=90)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 2.0, f"full_report took too long on 2500 runs: {elapsed:.2f}s")
        self.assertEqual(report["lifetime_runs"], 2500)
        self.assertGreater(report["rolling_windows"]["90d"]["runs"], report["rolling_windows"]["30d"]["runs"])
        self.assertGreater(report["rolling_windows"]["30d"]["runs"], report["rolling_windows"]["7d"]["runs"])


if __name__ == "__main__":
    unittest.main()
