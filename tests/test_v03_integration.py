from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path

from app.database import Database
from app.importer import import_path
from app.server import ApiHandler
from app.service import AppService


class V03IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = AppService(Path(__file__).resolve().parents[1], data_dir=self.root / "data")
        self.service.session_api_keys = {k: "" for k in self.service.session_api_keys}

        class Handler(ApiHandler):
            def log_message(self, *_args):
                pass

        Handler.service = self.service
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = f"127.0.0.1:{self.server.server_port}"
        self.origin = f"http://{self.host}"
        self.headers = {
            "Host": self.host,
            "Origin": self.origin,
            "Referer": self.origin + "/",
            "Sec-Fetch-Site": "same-origin",
        }

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.service.stop()
        self.temp.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None, headers: dict | None = None):
        body = json.dumps(payload).encode("utf-8") if method == "POST" and payload is not None else b""
        req_headers = dict(self.headers)
        if method == "POST":
            req_headers.update({"Content-Type": "application/json", "Content-Length": str(len(body))})
        if headers:
            req_headers.update(headers)
        with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)) as conn:
            conn.request(method, path, body=body, headers=req_headers)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()

    def test_load_sample_and_full_v03_workflow(self) -> None:
        # 1. Load bundled historical sample via POST /api/sample/load
        st, _, raw = self.request("POST", "/api/sample/load", {})
        self.assertEqual(st, 200)
        sample_res = json.loads(raw)
        self.assertEqual(sample_res["sample_date"], "2026-08-30")
        self.assertGreater(sample_res["runs_imported"], 50)

        # 2. Check intelligence, scenario trend, taxonomy, timeline, and hardware endpoints
        st, _, raw = self.request("GET", "/api/intelligence?days=90&date=2026-08-30")
        self.assertEqual(st, 200)
        intel = json.loads(raw)
        self.assertGreater(intel["lifetime_runs"], 50)
        self.assertIn("five_questions", intel)
        self.assertTrue(intel["diagnoses"])

        st, _, raw = self.request("GET", "/api/scenario/trend?scenario=1wall6targets%20small&days=90&date=2026-08-30")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(raw)["points"])

        st, _, _ = self.request("POST", "/api/taxonomy/tag", {"scenario": "1wall6targets small", "categories": ["static_clicking", "precise_switching"]})
        self.assertEqual(st, 200)
        st, _, raw = self.request("GET", "/api/taxonomy")
        self.assertEqual(json.loads(raw)["tags"]["1wall6targets small"]["source"], "manual")

        # 3. Readiness, plan generation & evaluation
        st, _, _ = self.request("POST", "/api/readiness", {"energy": 4, "focus": 4, "fatigue": 2, "eye_strain": 1, "wrist_discomfort": 1, "available_minutes": 30})
        self.assertEqual(st, 200)
        st, _, raw = self.request("POST", "/api/plans/generate", {"mode": "balanced_30", "date": "2026-08-30"})
        self.assertEqual(st, 200)
        plan = json.loads(raw)["plan"]
        st, _, _ = self.request("POST", "/api/plans/evaluate", {"plan_id": plan["id"], "subjective_feedback": "Good session"})
        self.assertEqual(st, 200)

        # 4. Offline coaching brief, Ask Q&A, effectiveness, hardware annotation & custom benchmark
        st, _, raw = self.request("POST", "/api/coach/generate", {"period": "monthly", "date": "2026-08-30", "use_llm": False})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(raw)["mode"], "local_deterministic")

        st, _, raw = self.request("POST", "/api/coach/ask", {"question": "What is holding back my aim?", "date": "2026-08-30"})
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(raw)["answer"])

        st, _, raw = self.request("POST", "/api/hardware", {"observed_at": "2026-08-20 12:00:00", "change_type": "mousepad", "summary": "Switched to control pad"})
        self.assertEqual(st, 200)
        hw_id = json.loads(raw)["event"]["id"]
        st, _, _ = self.request("POST", "/api/hardware/delete", {"id": hw_id})
        self.assertEqual(st, 200)

        st, _, raw = self.request("POST", "/api/benchmarks", {
            "name": "Sample Bench",
            "version": "1.0",
            "ranks": ["Silver", "Gold"],
            "scenarios": {"Pasu Voltaic Easy": {"Silver": 550, "Gold": 600}},
        })
        self.assertEqual(st, 200)
        bench = json.loads(raw)["custom_benchmarks"][0]
        self.assertEqual(bench["overall_complete_rank"], "Gold")

        # 5. Permanent memory deletion
        st, _, raw = self.request("POST", "/api/memory/note", {"kind": "hardware", "body": "Temp note"})
        self.assertEqual(st, 200)
        note_id = json.loads(raw)["notes"][0]["id"]
        st, _, raw = self.request("POST", "/api/memory/note/delete", {"id": note_id})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(raw)["notes"], [])

        # 6. Export CSV & JSON + static charts.js
        st, headers, csv_body = self.request("GET", "/api/export?format=csv&days=90&date=2026-08-30")
        self.assertEqual(st, 200)
        self.assertIn("text/csv", headers["Content-Type"])
        self.assertIn(b"1wall6targets small", csv_body)

        st, _, js_body = self.request("GET", "/charts.js")
        self.assertEqual(st, 200)
        self.assertIn(b"KovaakCharts", js_body)

    def test_security_controls_protect_all_new_endpoints(self) -> None:
        for method, path in [
            ("GET", "/api/intelligence"),
            ("GET", "/api/export?format=json"),
            ("POST", "/api/sample/load"),
            ("POST", "/api/coach/generate"),
            ("POST", "/api/plans/generate"),
            ("POST", "/api/memory/note/delete"),
        ]:
            with self.subTest(method=method, path=path):
                st, _, _ = self.request(method, path, payload={}, headers={"Origin": "https://evil.example"})
                self.assertEqual(st, 403)
                st_host, _, _ = self.request(method, path, payload={}, headers={"Host": "evil.example"})
                self.assertEqual(st_host, 403)

    def test_perf_diagnostics_and_v020_migration(self) -> None:
        # Unsupported .perf file detection without invented telemetry
        scan_dir = self.root / "stats_scan"
        scan_dir.mkdir()
        (scan_dir / "session.perf").write_bytes(b"\x00\x01PERFBINARY")
        report = import_path(self.service.database, scan_dir, force=True)
        self.assertEqual(report.summary()["unsupported_perf_files"], 1)

        # Simulated v0.2.0 DB opens cleanly under v0.3.0 schema without losing data
        v020_db_path = self.root / "v020.db"
        conn = sqlite3.connect(v020_db_path)
        conn.executescript("""
            CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE sources(path TEXT PRIMARY KEY, size_bytes INTEGER, mtime_ns INTEGER, rows_seen INTEGER, imported_at TEXT, status TEXT, error TEXT);
            CREATE TABLE runs(id INTEGER PRIMARY KEY AUTOINCREMENT, source_path TEXT, source_row INTEGER, row_hash TEXT, observed_at TEXT, scenario TEXT, score REAL, raw_json TEXT, UNIQUE(source_path, source_row));
            CREATE TABLE reports(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, provider TEXT, model TEXT, body TEXT, evidence_json TEXT, created_at TEXT);
            INSERT INTO sources VALUES('old.csv', 10, 10, 1, '2026-01-01', 'ready', NULL);
            INSERT INTO runs(source_path, source_row, row_hash, observed_at, scenario, score, raw_json) VALUES('old.csv', 1, 'h1', '2026-01-01 12:00:00', 'Air', 150.0, '{}');
        """)
        conn.close()
        migrated = Database(v020_db_path)
        runs = migrated.normalized_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["scenario"], "Air")


if __name__ == "__main__":
    unittest.main()
