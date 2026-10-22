from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

from app.database import Database
from app.records import (
    calculate_streaks,
    get_all_personal_bests,
    get_period_personal_bests,
    get_records,
    get_streaks,
)
from app.server import ApiHandler
from app.service import AppService



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


class RecordsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = AppService(self.root)
        self.service.session_api_keys = {key: "" for key in self.service.session_api_keys}

        class Handler(ApiHandler):
            def log_message(self, *_args):
                pass

        Handler.service = self.service
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = f"127.0.0.1:{self.server.server_port}"
        self.origin = "http://" + self.host

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.service.stop()
        self.temp.cleanup()

    def get(self, path):
        headers = {
            "Host": self.host,
            "Origin": self.origin,
            "Referer": self.origin + "/#progress",
            "Sec-Fetch-Site": "same-origin",
        }
        with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)) as conn:
            conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
            for k, v in headers.items():
                conn.putheader(k, v)
            conn.endheaders()
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read().decode("utf-8"))

    def test_records_endpoint_response_shape(self):
        # Insert synthetic runs
        with self.service.database.connect() as conn:
            conn.execute(
                "INSERT INTO sources(path, size_bytes, mtime_ns, status) VALUES('synth.csv', 10, 10, 'ready')"
            )
            conn.execute(
                """
                INSERT INTO runs(source_path, source_row, row_hash, observed_at, scenario, score, raw_json)
                VALUES
                ('synth.csv', 1, 'h1', '2026-10-20 10:00:00', 'Ascended Tracking', 100.0, '{}'),
                ('synth.csv', 2, 'h2', '2026-10-21 11:00:00', 'Ascended Tracking', 120.0, '{}'),
                ('synth.csv', 3, 'h3', '2026-10-22 12:00:00', 'Tile Frenzy', 50.0, '{}')
                """
            )

        status, headers, data = self.get("/api/records?period=week&date=2026-10-22")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertIn("no-store", headers.get("Cache-Control", ""))

        # Check expected shape keys
        self.assertIn("period", data)
        self.assertEqual(data["period"], "weekly")
        self.assertIn("start", data)
        self.assertEqual(data["start"], "2026-10-19")
        self.assertIn("end_exclusive", data)
        self.assertEqual(data["end_exclusive"], "2026-10-26")
        self.assertIn("current_streak", data)
        self.assertEqual(data["current_streak"], 3)
        self.assertIn("longest_streak", data)
        self.assertEqual(data["longest_streak"], 3)
        self.assertIn("records", data)
        self.assertIsInstance(data["records"], list)
        self.assertEqual(len(data["records"]), 3)

        # Check record items fields
        item = data["records"][0]
        for field in ("scenario", "score", "new_best", "previous_best", "delta", "observed_at", "date"):
            self.assertIn(field, item)

        # Ascended Tracking 120 beats 100 with delta 20
        pb_120 = next(r for r in data["records"] if r["score"] == 120.0)
        self.assertEqual(pb_120["scenario"], "Ascended Tracking")
        self.assertEqual(pb_120["previous_best"], 100.0)
        self.assertEqual(pb_120["delta"], 20.0)

        # Test default parameters
        default_status, _, default_data = self.get("/api/records")
        self.assertEqual(default_status, 200)
        self.assertIn("records", default_data)
        self.assertIn("current_streak", default_data)


if __name__ == "__main__":
    unittest.main()

