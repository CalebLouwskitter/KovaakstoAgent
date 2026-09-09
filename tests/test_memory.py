from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from app.database import Database
from app.importer import import_file
from app.memory import CoachingMemory, period_bounds
from app.server import ApiHandler
from app.service import AppService


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = AppService(self.root)
        self.path = self.root / "live.csv"

    def tearDown(self):
        self.service.stop()
        self.temp.cleanup()

    def capture(self, rows):
        self.path.write_text("Timestamp,Scenario,Score,Hits,Shots,Duration\n" + "\n".join(rows) + "\n", encoding="utf-8")
        import_file(self.service.database, self.path)

    def test_rewrite_truncate_correction_and_restart_preserve_history(self):
        self.capture(["2024-02-29 10:00:00,Air,100,8,10,60", "2024-03-01 10:00:00,Air,120,9,10,60"])
        self.capture(["2024-03-01 10:00:00,Air,125,9,10,60"])
        self.capture(["2025-01-01 10:00:00,Air,150,9,10,60"])
        self.path.unlink()
        reopened = AppService(self.root)
        report = reopened.memory.dashboard("yearly", "2024-06-01")
        self.assertEqual(report["summary"]["runs"], 2)
        self.assertEqual(report["scenarios"][0]["best_score"], 125)
        self.assertEqual(report["lifetime"]["runs"], 3)

    def test_inferred_timestamps_do_not_duplicate_on_file_touch(self):
        self.path.write_text("Scenario,Score\nAir,100\nAir,100\n", encoding="utf-8")
        import_file(self.service.database, self.path)
        stamp = self.path.stat().st_mtime
        os.utime(self.path, (stamp + 120, stamp + 120))
        import_file(self.service.database, self.path)
        self.assertEqual(self.service.memory.dashboard()["lifetime"]["runs"], 2)

    def test_existing_database_migration_is_idempotent(self):
        self.capture(["2024-01-01 10:00:00,Air,100,8,10,60"])
        with self.service.database.connect() as connection:
            connection.execute("DROP VIEW history_metrics")
            connection.execute("DROP TABLE run_history")
            connection.execute("DELETE FROM settings WHERE key='history_migrated'")
        reopened = AppService(self.root)
        reopened = AppService(self.root)
        self.assertEqual(reopened.memory.dashboard("yearly", "2024-01-01")["summary"]["runs"], 1)

    def test_calendar_boundaries_leap_year_and_previous_month(self):
        self.assertEqual(period_bounds("yearly", "2024-02-29"), ("2024-01-01", "2025-01-01", "2023-01-01"))
        self.assertEqual(period_bounds("monthly", "2024-03-31"), ("2024-03-01", "2024-04-01", "2024-02-01"))
        self.assertEqual(period_bounds("weekly", "2025-01-01"), ("2024-12-30", "2025-01-06", "2024-12-23"))
        self.capture(["2024-02-28 23:59:59,Air,100,8,10,60", "2024-02-29 00:00:00,Air,120,9,10,60", "2024-03-01 00:00:00,Air,140,9,10,60"])
        self.assertEqual(self.service.memory.dashboard("daily", "2024-02-29")["summary"]["runs"], 1)
        self.assertEqual(self.service.memory.dashboard("monthly", "2024-02-29")["summary"]["runs"], 2)
        with self.assertRaises(ValueError):
            self.service.memory.dashboard("forever")

    def test_accuracy_uses_paired_records_and_scores_stay_per_scenario(self):
        self.capture(["2024-01-01 10:00:00,Air,100,8,10,60", "2024-01-01 10:01:00,Air,120,,100,60", "2024-01-01 10:02:00,Flick,9000,1,10,60"])
        dashboard = self.service.memory.dashboard("monthly", "2024-01-01")
        self.assertEqual(dashboard["summary"]["average_accuracy"], 45)
        self.assertNotIn("average_score", dashboard["summary"])
        self.assertEqual(dashboard["summary"]["paired_accuracy_runs"], 2)
        self.assertEqual(len(dashboard["scenarios"]), 2)

    def test_every_provider_and_custom_role_receive_shared_memory_after_restart(self):
        self.capture(["2024-01-01 10:00:00,Air,100,8,10,60", "2025-01-01 10:00:00,Air,120,9,10,60"])
        note = self.service.memory.save_note({"kind": "goal", "body": "Train 20 minutes on weekdays"})["notes"][0]
        self.service.memory.save_note({"kind": "outcome", "body": "Tried the slower tracking routine", "scenario": "Air"})
        self.service.memory.save_note({"kind": "note", "body": "ARCHIVED_SECRET", "status": "archived"})
        role = self.service.memory.save_role({"name": "Precision coach", "instructions": "Focus on precision."})
        reopened = AppService(self.root)
        self.assertEqual(next(item for item in reopened.memory.notes() if item["id"] == note["id"])["status"], "active")
        self.assertEqual(reopened.memory.role(role["id"])["name"], "Precision coach")
        for provider in ("openai", "openrouter", "gemini"):
            reopened.update_settings("", "test-model", provider)
            client = Mock()
            client.respond.return_value = "### Priorities\n\nRecord one outcome next session."
            with patch("app.service.create_model_client", return_value=client):
                report = reopened.create_digest("yearly", "2025-01-01", role["id"])
            evidence = json.loads(client.respond.call_args.args[1].split("\n", 1)[1])
            self.assertEqual(evidence["lifetime"]["runs"], 2)
            self.assertTrue(any(item["id"] == note["id"] for item in evidence["player_memory"]))
            self.assertEqual(evidence["role"]["id"], role["id"])
            self.assertNotIn(str(self.root), client.respond.call_args.args[1])
            self.assertNotIn("ARCHIVED_SECRET", client.respond.call_args.args[1])
            self.assertEqual(report["provider"], provider)
        self.assertEqual(len(reopened.memory.report_history()), 3)
        self.assertTrue(evidence["prior_advice_not_verified_facts"])

    def test_context_is_bounded_without_deleting_old_memory(self):
        self.capture([f"2024-01-01 10:00:00,Scenario {index},{index},8,10,60" for index in range(50)])
        for index in range(30):
            self.service.memory.save_note({"kind": "goal", "body": f"Goal {index}"})
        evidence = self.service.memory.evidence("yearly", "2024-01-01")
        self.assertEqual(evidence["coverage"]["scenarios_total"], 50)
        self.assertEqual(len(evidence["scenarios"]), 40)
        self.assertEqual(len(evidence["recent_runs"]), 20)
        self.assertEqual(len(evidence["player_memory"]), 24)
        self.assertEqual(len(self.service.memory.notes()), 30)
        self.assertEqual(evidence["summary"]["runs"], 50)

    def test_no_model_call_for_empty_period_or_invalid_role(self):
        with patch("app.service.create_model_client") as client:
            with self.assertRaises(ValueError):
                self.service.create_digest("yearly", "2024-01-01")
            with self.assertRaises(ValueError):
                self.service.create_digest("yearly", "2024-01-01", "missing")
            client.assert_not_called()

    def test_year_end_catches_up_once_and_respects_pause_across_restart(self):
        self.capture(["2023-01-01 10:00:00,Air,100,8,10,60", "2024-12-31 23:59:59,Air,120,9,10,60"])
        with patch("app.service.create_model_client") as client:
            self.assertEqual(self.service.memory.run_due_reviews(datetime(2024, 12, 31, 23, 59, 59)), [2023])
            self.assertEqual(self.service.memory.run_due_reviews(datetime(2025, 1, 1)), [2024])
            self.assertEqual(AppService(self.root).memory.run_due_reviews(datetime(2025, 6, 1)), [])
            client.assert_not_called()
        self.capture(["2025-06-01 10:00:00,Air,150,9,10,60"])
        self.service.database.set_setting("annual_review_enabled", "false")
        reopened = AppService(self.root)
        self.assertEqual(reopened.memory.run_due_reviews(datetime(2026, 1, 1)), [])
        reopened.database.set_setting("annual_review_enabled", "true")
        self.assertEqual(reopened.memory.run_due_reviews(datetime(2026, 1, 1)), [2025])

    def test_concurrent_schedulers_do_not_duplicate_reviews(self):
        self.capture(["2024-01-01 10:00:00,Air,100,8,10,60"])
        other = CoachingMemory(self.service.database)
        threads = [threading.Thread(target=memory.run_due_reviews, args=(datetime(2025, 1, 1),)) for memory in (self.service.memory, other)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(self.service.memory.annual_reviews()), 1)

    def test_failed_capture_signature_is_retried(self):
        self.service.database.upsert_source(str(self.path), 10, 20, "error", "Temporary parse failure")
        self.assertIsNone(self.service.database.source_signature(str(self.path)))

    def test_failed_partial_import_rolls_back_and_can_be_retried(self):
        self.path.write_text("Timestamp,Scenario,Score\n2024-01-01 10:00:00,Air,100\n2024-01-01 10:01:00,Air,120\n", encoding="utf-8")
        original = self.service.database.upsert_run
        def fail_second(run):
            if run["source_row"] == 2:
                raise ValueError("Interrupted capture")
            return original(run)
        with patch.object(self.service.database, "upsert_run", side_effect=fail_second):
            with self.assertRaises(ValueError):
                import_file(self.service.database, self.path)
        self.assertEqual(self.service.memory.dashboard()["lifetime"]["runs"], 0)
        self.assertIsNone(self.service.database.source_signature(str(self.path)))
        import_file(self.service.database, self.path)
        self.assertEqual(self.service.memory.dashboard()["lifetime"]["runs"], 2)

    def test_late_historical_data_refreshes_annual_review(self):
        self.capture(["2024-01-01 10:00:00,Air,100,8,10,60"])
        self.service.memory.run_due_reviews(datetime(2025, 1, 1))
        self.capture(["2024-02-01 10:00:00,Air,150,9,10,60"])
        self.assertEqual(self.service.memory.run_due_reviews(datetime(2025, 1, 2)), [2024])
        review = self.service.memory.annual_reviews()[0]
        self.assertIn("2 retained runs", review["body"])
        self.assertIn("150.00", review["body"])
        self.assertEqual(self.service.memory.run_due_reviews(datetime(2025, 1, 3)), [])

    def test_http_dashboard_notes_roles_schedule_and_consistent_backup(self):
        service = self.service
        class Handler(ApiHandler):
            def log_message(self, *_args): pass
        Handler.service = service
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        def request(path, payload=None):
            data = json.dumps(payload).encode() if payload is not None else None
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})) as response:
                return response.read()
        try:
            request("/api/memory/note", {"kind": "goal", "body": "Keep this across restarts"})
            role = json.loads(request("/api/memory/role", {"name": "New coach", "instructions": "Help me practice"}))
            self.assertIn("id", role)
            self.assertEqual(json.loads(request("/api/dashboard?period=yearly&date=2024-01-01"))["summary"]["runs"], 0)
            request("/api/reviews/schedule", {"enabled": False})
            self.assertFalse(json.loads(request("/api/memory"))["schedule"]["enabled"])
            with self.assertRaises(urllib.error.HTTPError) as error:
                request("/api/reviews/schedule", {"enabled": "false"})
            self.assertEqual(error.exception.code, 400)
            backup = self.root / "backup.db"
            backup.write_bytes(request("/api/backup"))
            with closing(sqlite3.connect(backup)) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("SELECT body FROM coaching_notes").fetchone()[0], "Keep this across restarts")
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main()
