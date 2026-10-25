from __future__ import annotations

import os
import random
import tempfile
import time
import unittest
from pathlib import Path

from app.database import Database
from app.importer import ImportReport, ImportResult, import_file, import_path

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "import"


class ImporterHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = Database(self.root / "test.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_fixture_normal(self) -> None:
        path = FIXTURES_DIR / "normal.csv"
        result = import_file(self.database, path)
        self.assertEqual(result.outcome, "imported")
        self.assertIsNone(result.reason)
        self.assertEqual(result.inserted, 1)
        run = self.database.recent_runs(1)[0]
        self.assertEqual(run["scenario"], "Synthetic Normal")
        self.assertEqual(run["score"], 80.0)

    def test_fixture_utf8_bom(self) -> None:
        path = FIXTURES_DIR / "utf8_bom.csv"
        result = import_file(self.database, path)
        self.assertEqual(result.outcome, "imported")
        self.assertIsNone(result.reason)
        self.assertEqual(result.inserted, 1)
        run = self.database.recent_runs(1)[0]
        self.assertEqual(run["scenario"], "Synthetic UTF8 BOM")
        self.assertEqual(run["score"], 95.0)

    def test_fixture_utf16(self) -> None:
        path = FIXTURES_DIR / "utf16.csv"
        result = import_file(self.database, path)
        self.assertEqual(result.outcome, "imported")
        self.assertIsNone(result.reason)
        self.assertEqual(result.inserted, 1)
        run = self.database.recent_runs(1)[0]
        self.assertEqual(run["scenario"], "Synthetic UTF16")
        self.assertEqual(run["score"], 110.0)

    def test_fixture_windows_line_endings(self) -> None:
        path = FIXTURES_DIR / "windows_line_endings.csv"
        result = import_file(self.database, path)
        self.assertEqual(result.outcome, "imported")
        self.assertIsNone(result.reason)
        self.assertEqual(result.inserted, 1)
        run = self.database.recent_runs(1)[0]
        self.assertEqual(run["scenario"], "Synthetic Windows CRLF")
        self.assertEqual(run["score"], 120.0)

    def test_fixture_semicolon_delimiter(self) -> None:
        path = FIXTURES_DIR / "semicolon_delimiter.csv"
        result = import_file(self.database, path)
        self.assertEqual(result.outcome, "imported")
        self.assertIsNone(result.reason)
        self.assertEqual(result.inserted, 1)
        run = self.database.recent_runs(1)[0]
        self.assertEqual(run["scenario"], "Synthetic Semicolon")
        self.assertEqual(run["score"], 135.0)

    def test_fixture_european_decimal_comma(self) -> None:
        path = FIXTURES_DIR / "european_decimal_comma.csv"
        result = import_file(self.database, path)
        self.assertEqual(result.outcome, "imported")
        self.assertIsNone(result.reason)
        self.assertEqual(result.inserted, 1)
        run = self.database.recent_runs(1)[0]
        self.assertEqual(run["scenario"], "Synthetic European Decimal")
        self.assertEqual(run["score"], 145.5)
        self.assertEqual(run["accuracy"], 81.2)

    def test_fixture_missing_score(self) -> None:
        path = FIXTURES_DIR / "missing_score.csv"
        result = import_file(self.database, path)
        self.assertEqual(result.outcome, "skipped")
        self.assertEqual(result.reason, "incomplete")
        self.assertEqual(result.inserted, 0)

    def test_fixture_extra_unknown_keys(self) -> None:
        path = FIXTURES_DIR / "extra_unknown_keys.csv"
        result = import_file(self.database, path)
        self.assertEqual(result.outcome, "imported")
        self.assertIsNone(result.reason)
        self.assertEqual(result.inserted, 1)
        run = self.database.recent_runs(1)[0]
        self.assertEqual(run["scenario"], "Synthetic Extra Keys")
        self.assertEqual(run["score"], 160.0)
        self.assertEqual(run["raw"]["NewGameEngineSetting"], "Ultra")

    def test_fixture_truncated_writing(self) -> None:
        path = FIXTURES_DIR / "truncated_writing.csv"
        result = import_file(self.database, path)
        self.assertEqual(result.outcome, "skipped")
        self.assertEqual(result.reason, "incomplete")
        self.assertEqual(result.inserted, 0)

    def test_fixture_empty(self) -> None:
        path = FIXTURES_DIR / "empty.csv"
        result = import_file(self.database, path)
        self.assertEqual(result.outcome, "skipped")
        self.assertEqual(result.reason, "empty")
        self.assertEqual(result.inserted, 0)

    def test_fixture_zero_byte(self) -> None:
        path = FIXTURES_DIR / "zero_byte.csv"
        result = import_file(self.database, path)
        self.assertEqual(result.outcome, "skipped")
        self.assertEqual(result.reason, "empty")
        self.assertEqual(result.inserted, 0)

    def test_fixture_non_ascii_scenario(self) -> None:
        path = FIXTURES_DIR / "🎯 Non-ASCII Scenario - Challenge - 2026.08.30-15.00.00 Stats.csv"
        result = import_file(self.database, path)
        self.assertEqual(result.outcome, "imported")
        self.assertIsNone(result.reason)
        self.assertEqual(result.inserted, 1)
        run = self.database.recent_runs(1)[0]
        self.assertEqual(run["scenario"], "🎯 Non-ASCII Scenario")
        self.assertEqual(run["score"], 175.0)

    def test_fixture_old_version_layout(self) -> None:
        path = FIXTURES_DIR / "old_version_layout.csv"
        result = import_file(self.database, path)
        self.assertEqual(result.outcome, "imported")
        self.assertIsNone(result.reason)
        self.assertEqual(result.inserted, 1)
        run = self.database.recent_runs(1)[0]
        self.assertEqual(run["scenario"], "Vintage Scenario")
        self.assertEqual(run["score"], 500.0)
        self.assertIsNone(run["dpi"])
        self.assertIsNone(run["sensitivity"])

    def test_import_report_summary_and_compatibility(self) -> None:
        # Import all fixtures in the directory via import_path with force=True
        report = import_path(self.database, FIXTURES_DIR, force=True)
        self.assertIsInstance(report, ImportReport)
        self.assertEqual(len(report), 13)
        self.assertEqual(len(report.results), 13)

        summary = report.summary()
        self.assertEqual(summary["total"], 13)
        self.assertEqual(summary["outcomes"]["imported"], 9)
        self.assertEqual(summary["outcomes"]["skipped"], 4)
        self.assertEqual(summary["reasons"]["incomplete"], 2)
        self.assertEqual(summary["reasons"]["empty"], 2)

        # Callers using list comprehension work seamlessly
        dict_results = [r.to_dict() for r in report]
        self.assertEqual(len(dict_results), 13)
        self.assertTrue(all("outcome" in d and "reason" in d for d in dict_results))

        # Re-importing without force detects duplicates (simulating time in future so 2s check does not trigger)
        repeat_report = import_path(self.database, FIXTURES_DIR, force=False, now=time.time() + 10)
        repeat_summary = repeat_report.summary()
        # Ready files (9 imported + 2 empty) are duplicates; 2 incomplete files retry
        self.assertEqual(repeat_summary["reasons"].get("duplicate", 0), 11)

    def test_recent_file_skipped_as_incomplete_and_retried(self) -> None:
        target = self.root / "recent.csv"
        target.write_text(
            "Scenario:,Recent Test\nScore:,200.0\nAccuracy:,90%\nDuration:,60\n",
            encoding="utf-8",
        )
        # File was just written, mtime is current time (< 2s ago)
        report1 = import_path(self.database, target, force=False)
        self.assertEqual(len(report1), 1)
        self.assertEqual(report1[0].reason, "incomplete")
        self.assertEqual(report1[0].inserted, 0)
        self.assertEqual(len(self.database.recent_runs(10)), 0)

        # Now simulate 3 seconds elapsed
        now_future = time.time() + 3.0
        report2 = import_path(self.database, target, force=False, now=now_future)
        self.assertEqual(len(report2), 1)
        self.assertEqual(report2[0].outcome, "imported")
        self.assertIsNone(report2[0].reason)
        self.assertEqual(report2[0].inserted, 1)
        self.assertEqual(len(self.database.recent_runs(10)), 1)

    def test_property_malformed_fuzz(self) -> None:
        """Mutate a valid file 200 times and assert import_path never raises an exception."""
        valid_template = (FIXTURES_DIR / "normal.csv").read_text(encoding="utf-8")
        fuzz_dir = self.root / "fuzz"
        fuzz_dir.mkdir()

        rng = random.Random(42)
        delimiters = [",", ";", "\t", "|", ":", " ", ""]

        for i in range(200):
            content = valid_template
            mutation_type = rng.choice(["truncate", "drop_lines", "swap_delimiter", "insert_junk", "combo"])

            if mutation_type in ("truncate", "combo"):
                cutoff = rng.randint(0, len(content))
                content = content[:cutoff]

            if mutation_type in ("drop_lines", "combo") and content:
                lines = content.splitlines(keepends=True)
                if lines:
                    keep_count = rng.randint(0, len(lines))
                    lines = rng.sample(lines, keep_count)
                    content = "".join(lines)

            if mutation_type in ("swap_delimiter", "combo") and content:
                target_del = rng.choice(delimiters)
                content = content.replace(",", target_del)

            if mutation_type in ("insert_junk", "combo") and content:
                junk = "".join(chr(rng.randint(0, 255)) for _ in range(rng.randint(1, 20)))
                pos = rng.randint(0, len(content))
                content = content[:pos] + junk + content[pos:]

            filename = f"fuzz_{i:03d}.csv"
            filepath = fuzz_dir / filename
            try:
                filepath.write_text(content, encoding="utf-8", errors="ignore")
            except Exception:
                filepath.write_bytes(content.encode("utf-8", errors="ignore"))

            # Set mtime back so the 2-second check does not mark all of them incomplete
            past_time = time.time() - 10
            os.utime(filepath, (past_time, past_time))

        # Import all 200 fuzzed files
        report = import_path(self.database, fuzz_dir, force=True)
        self.assertEqual(len(report), 200)
        self.assertEqual(len(report.results), 200)

        # Assert every outcome is valid and never raised
        valid_outcomes = {"imported", "skipped", "error"}
        for r in report:
            self.assertIn(r.outcome, valid_outcomes)
            self.assertGreaterEqual(r.rows_seen, 0)
            self.assertGreaterEqual(r.inserted, 0)


if __name__ == "__main__":
    unittest.main()
