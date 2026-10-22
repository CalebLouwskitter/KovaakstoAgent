from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app.database import Database
from app.records import (
    calculate_streaks,
    get_all_personal_bests,
    get_period_personal_bests,
    get_records,
    get_streaks,
)


class RecordsQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = Database(self.root / "records_test.db")

    def tearDown(self):
        self.temp.cleanup()

    def insert_run(self, scenario: str, score: float, observed_at: str, path: str = "test.csv", row: int = 1):
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO sources(path, size_bytes, mtime_ns, status)
                VALUES(?, 100, 100, 'ready')
                """,
                (path,),
            )
            conn.execute(
                """
                INSERT INTO runs(
                    source_path, source_row, row_hash, observed_at, scenario, score, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, '{}')
                """,
                (path, row, f"hash_{path}_{row}", observed_at, scenario, score),
            )

    def test_empty_database(self):
        streaks = get_streaks(self.database, as_of="2026-10-22")
        self.assertEqual(streaks["current"], 0)
        self.assertEqual(streaks["longest"], 0)

        pbs = get_all_personal_bests(self.database)
        self.assertEqual(pbs, [])

        records = get_records(self.database, period="week", anchor="2026-10-22")
        self.assertEqual(records["current_streak"], 0)
        self.assertEqual(records["longest_streak"], 0)
        self.assertEqual(records["records"], [])
        self.assertEqual(records["all_time_pbs"], [])

    def test_personal_best_detection(self):
        # Initial run sets initial PB with no previous best
        self.insert_run("Tile Frenzy", 100.0, "2026-10-01 12:00:00", row=1)
        pbs = get_all_personal_bests(self.database)
        self.assertEqual(len(pbs), 1)
        self.assertEqual(pbs[0]["scenario"], "Tile Frenzy")
        self.assertEqual(pbs[0]["score"], 100.0)
        self.assertEqual(pbs[0]["new_best"], 100.0)
        self.assertIsNone(pbs[0]["previous_best"])
        self.assertIsNone(pbs[0]["delta"])
        self.assertEqual(pbs[0]["date"], "2026-10-01")

        # Lower score does not update PB
        self.insert_run("Tile Frenzy", 95.0, "2026-10-02 12:00:00", row=2)
        pbs = get_all_personal_bests(self.database)
        self.assertEqual(len(pbs), 1)
        self.assertEqual(pbs[0]["score"], 100.0)
        self.assertIsNone(pbs[0]["previous_best"])

        # Higher score sets new PB with previous best and delta
        self.insert_run("Tile Frenzy", 125.5, "2026-10-03 12:00:00", row=3)
        pbs = get_all_personal_bests(self.database)
        self.assertEqual(len(pbs), 1)
        self.assertEqual(pbs[0]["score"], 125.5)
        self.assertEqual(pbs[0]["new_best"], 125.5)
        self.assertEqual(pbs[0]["previous_best"], 100.0)
        self.assertEqual(pbs[0]["delta"], 25.5)
        self.assertEqual(pbs[0]["date"], "2026-10-03")

    def test_ties_do_not_count_as_new_personal_bests(self):
        self.insert_run("1wall6targets", 500.0, "2026-10-10 10:00:00", row=1)
        # Exact tie on score
        self.insert_run("1wall6targets", 500.0, "2026-10-11 10:00:00", row=2)

        pbs = get_all_personal_bests(self.database)
        self.assertEqual(len(pbs), 1)
        self.assertEqual(pbs[0]["score"], 500.0)
        # Original date should be retained from the first time the score was achieved
        self.assertEqual(pbs[0]["date"], "2026-10-10")
        self.assertIsNone(pbs[0]["previous_best"])

        # Another tie after establishing a new PB
        self.insert_run("1wall6targets", 600.0, "2026-10-12 10:00:00", row=3)
        self.insert_run("1wall6targets", 600.0, "2026-10-13 10:00:00", row=4)

        pbs = get_all_personal_bests(self.database)
        self.assertEqual(len(pbs), 1)
        self.assertEqual(pbs[0]["score"], 600.0)
        self.assertEqual(pbs[0]["date"], "2026-10-12")
        self.assertEqual(pbs[0]["previous_best"], 500.0)
        self.assertEqual(pbs[0]["delta"], 100.0)

        # Check period PBs for 2026-10-13 (the tied run day)
        period_pbs, _, _ = get_period_personal_bests(self.database, period="daily", anchor="2026-10-13")
        self.assertEqual(period_pbs, [], "A tied run must not produce a PB record in that period")

    def test_streaks_across_a_gap(self):
        # Runs on 2026-01-01, 2026-01-02 (2-day streak)
        self.insert_run("Air", 10.0, "2026-01-01 09:00:00", row=1)
        self.insert_run("Air", 11.0, "2026-01-01 18:00:00", row=2)  # Multiple runs on same day
        self.insert_run("Air", 12.0, "2026-01-02 10:00:00", row=3)

        # Gap on 2026-01-03

        # Runs on 2026-01-04, 2026-01-05, 2026-01-06 (3-day streak)
        self.insert_run("Air", 13.0, "2026-01-04 10:00:00", row=4)
        self.insert_run("Air", 14.0, "2026-01-05 10:00:00", row=5)
        self.insert_run("Air", 15.0, "2026-01-06 10:00:00", row=6)

        # As of 2026-01-06 (active day)
        streaks = get_streaks(self.database, as_of="2026-01-06")
        self.assertEqual(streaks["longest"], 3)
        self.assertEqual(streaks["current"], 3)

        # As of 2026-01-07 (day after without practice yet)
        streaks = get_streaks(self.database, as_of="2026-01-07")
        self.assertEqual(streaks["longest"], 3)
        self.assertEqual(streaks["current"], 3)

        # As of 2026-01-08 (2 days after, streak broken)
        streaks = get_streaks(self.database, as_of="2026-01-08")
        self.assertEqual(streaks["longest"], 3)
        self.assertEqual(streaks["current"], 0)

    def test_period_filtering_and_database_helpers(self):
        # Week of 2026-10-19 to 2026-10-25
        self.insert_run("Frenzy", 100.0, "2026-10-18 20:00:00", row=1)  # Prior week Sunday
        self.insert_run("Frenzy", 110.0, "2026-10-20 10:00:00", row=2)  # In target week
        self.insert_run("Track", 200.0, "2026-10-21 14:00:00", row=3)   # In target week
        self.insert_run("Frenzy", 120.0, "2026-10-22 15:00:00", row=4)  # In target week

        res = self.database.records(period="week", anchor="2026-10-22")
        self.assertEqual(res["period"], "weekly")
        self.assertEqual(res["start"], "2026-10-19")
        self.assertEqual(res["end_exclusive"], "2026-10-26")

        # Three PBs in this week (Frenzy 120, Track 200, Frenzy 110)
        scenarios_in_period = [item["scenario"] for item in res["records"]]
        self.assertIn("Frenzy", scenarios_in_period)
        self.assertIn("Track", scenarios_in_period)
        self.assertEqual(len(res["records"]), 3)

        # Daily view for 2026-10-20
        daily = self.database.records(period="day", anchor="2026-10-20")
        self.assertEqual(len(daily["records"]), 1)
        self.assertEqual(daily["records"][0]["scenario"], "Frenzy")
        self.assertEqual(daily["records"][0]["new_best"], 110.0)
        self.assertEqual(daily["records"][0]["previous_best"], 100.0)
        self.assertEqual(daily["records"][0]["delta"], 10.0)


if __name__ == "__main__":
    unittest.main()
