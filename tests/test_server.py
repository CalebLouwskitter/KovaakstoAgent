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
from unittest.mock import Mock, patch

from app.server import ApiHandler, run
from app.service import AppService


class LocalRequestTests(unittest.TestCase):
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

    def request(self, method, path, payload=None, headers=None, raw_headers=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        fields = {"Host": self.host}
        if method == "POST":
            fields.update({"Content-Type": "application/json", "Content-Length": str(len(body))})
        fields.update(headers or {})
        with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)) as conn:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for key, value in (raw_headers if raw_headers is not None else fields.items()):
                if value is not None:
                    conn.putheader(key, value)
            conn.endheaders(body)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()

    def test_same_origin_workflow_and_backup_survive_restart(self):
        headers = {"Origin": self.origin, "Referer": self.origin + "/#memory", "Sec-Fetch-Site": "same-origin"}
        sample = self.root / "sample.csv"
        sample.write_text("Timestamp,Scenario,Score\n2026-09-09 09:00:00,Demo,100\n")
        for path, payload in [
            ("/api/settings", {"watch_path": str(sample), "provider": "openai"}),
            ("/api/import", {"force": True}),
            ("/api/memory/note", {"kind": "goal", "body": "Keep progress"}),
            ("/api/memory/role", {"name": "Demo coach", "instructions": "Review consistency"}),
            ("/api/reviews/schedule", {"enabled": False}),
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.request("POST", path, payload, headers)[0], 200)
        status, _, body = self.request("GET", "/api/dashboard?period=daily&date=2026-09-09", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["summary"]["runs"], 1)
        status, response_headers, backup = self.request("GET", "/api/backup", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(response_headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertNotIn("Access-Control-Allow-Origin", response_headers)
        restored = self.root / "restore" / "data" / "kovaaks-agent.db"
        restored.parent.mkdir(parents=True)
        restored.write_bytes(backup)
        with closing(sqlite3.connect(restored)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        reopened = AppService(restored.parents[1])
        try:
            self.assertEqual(reopened.memory.notes()[0]["body"], "Keep progress")
            self.assertEqual(reopened.memory.dashboard()["lifetime"]["runs"], 1)
        finally:
            reopened.stop()

    def test_foreign_hosts_cannot_read_backup_or_change_memory(self):
        for host in ["untrusted.example", "127.0.0.1.evil.test", "localhost.evil.test", "localhost:1", "127.0.0.1", "127.1", None]:
            for method, path in [("GET", "/api/backup"), ("GET", "/api/status"), ("GET", "/"), ("POST", "/api/memory/note")]:
                with self.subTest(host=host, path=path):
                    status, _, body = self.request(method, path, {"body": "Must not persist"}, {"Host": host})
                    self.assertEqual(status, 403)
                    self.assertNotIn(b"SQLite format", body)
        self.assertEqual(self.service.memory.notes(), [])

    def test_cross_origin_requests_do_not_reach_sensitive_actions(self):
        self.service.create_digest = Mock(side_effect=AssertionError("Provider must not be called"))
        self.service.test_model = Mock(side_effect=AssertionError("Provider must not be called"))
        bad_headers = [
            {"Origin": "https://untrusted.example"}, {"Origin": "null"},
            {"Origin": "http://localhost:" + str(self.server.server_port)},
            {"Origin": self.origin + "0"}, {"Origin": self.origin + "/"},
            {"Origin": self.origin + "@evil.test"}, {"Origin": "https://" + self.host},
            {"Referer": "https://untrusted.example/page"},
            {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
        ]
        for headers in bad_headers:
            for method, path in [("GET", "/api/backup"), ("POST", "/api/digest"), ("POST", "/api/model/test"), ("POST", "/api/memory/note")]:
                with self.subTest(headers=headers, path=path):
                    self.assertEqual(self.request(method, path, {"body": "Must not persist"}, headers)[0], 403)
        self.service.create_digest.assert_not_called()
        self.service.test_model.assert_not_called()
        self.assertEqual(self.service.memory.notes(), [])

    def test_json_required_even_without_browser_metadata(self):
        for content_type in [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=demo", "application/jsonx"]:
            with self.subTest(content_type=content_type):
                self.assertEqual(self.request("POST", "/api/memory/note", {"body": "Must not persist"}, {"Content-Type": content_type})[0], 415)
        self.assertEqual(self.service.memory.notes(), [])
        self.assertEqual(self.request("POST", "/api/memory/note", {"body": "Local client"}, {"Content-Type": "application/json; charset=utf-8"})[0], 200)

    def test_duplicate_security_headers_are_rejected(self):
        for key, value in [("Host", self.host), ("Origin", self.origin), ("Referer", self.origin + "/"), ("Sec-Fetch-Site", "same-origin"), ("Content-Type", "application/json")]:
            headers = [("Host", self.host), ("Content-Length", "2"), ("Content-Type", "application/json")]
            if key not in {"Host", "Content-Type"}:
                headers.append((key, value))
            headers.append((key, value))
            with self.subTest(key=key):
                self.assertEqual(self.request("POST", "/api/memory/note", {}, raw_headers=headers)[0], 415 if key == "Content-Type" else 403)

    def test_invalid_body_framing_is_rejected_without_reading(self):
        for lengths in [[], [("Content-Length", "-1")], [("Content-Length", "1000001")], [("Content-Length", "x")], [("Content-Length", "2"), ("Content-Length", "2")], [("Content-Length", "2"), ("Transfer-Encoding", "chunked")]]:
            with self.subTest(lengths=lengths):
                headers = [("Host", self.host), ("Content-Type", "application/json")] + lengths
                self.assertEqual(self.request("POST", "/api/memory/note", {}, raw_headers=headers)[0], 400)

    def test_local_navigation_alias_and_preflight(self):
        for authority in [self.host, f"localhost:{self.server.server_port}"]:
            status, headers, body = self.request("GET", "/", headers={"Host": authority, "Sec-Fetch-Site": "none"})
            self.assertEqual(status, 200)
            self.assertIn(b"Training workspace", body)
            self.assertEqual(headers["X-Frame-Options"], "DENY")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        status, headers, _ = self.request("OPTIONS", "/api/digest", headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST"})
        self.assertEqual(status, 501)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_nonlocal_bind_rejected_before_service_creation(self):
        with patch("app.server.AppService") as service:
            for host in ["0.0.0.0", "192.168.1.2", "example.com"]:
                with self.subTest(host=host), self.assertRaises(ValueError):
                    run(host=host, open_browser=False)
            service.assert_not_called()


if __name__ == "__main__":
    unittest.main()
