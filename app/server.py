"""Embedded HTTP server and REST API for Kovaak Agent.

Exposes local REST endpoints:
- GET  /api/status        -> Application, watcher, and 7-day performance status
- GET  /api/runs          -> Recent normalized runs (limit parameter)
- GET  /api/summary       -> Rolling aggregate metrics (days parameter)
- GET  /api/digest/latest -> Most recent cached coaching digest
- POST /api/settings      -> Update watch path, model, or provider
- POST /api/import        -> Trigger manual on-demand CSV scan
- POST /api/model/key     -> Store session API key in memory
- POST /api/model/test    -> Run connection test with active provider
- POST /api/digest        -> Generate a selected-period AI coaching report

Serves static UI files (/index.html, /app.js, /styles.css, /vendor/daisyui.css) from the 'web' folder.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import signal
import sqlite3
import sys
import tempfile
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .model import ModelError
from .service import AppService


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"


class ApiHandler(BaseHTTPRequestHandler):
    """HTTP request handler routing REST API calls and static file requests."""
    service: AppService

    def log_message(self, fmt: str, *args) -> None:
        """Format request logs with a '[local]' prefix."""
        sys.stdout.write("[local] " + (fmt % args) + "\n")

    def _json(self, payload, status: int = 200) -> None:
        """Serialize and transmit a UTF-8 JSON response with no-store cache headers."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def _local_request(self, *, write: bool = False) -> bool:
        """Reject foreign browser requests before reading data or invoking actions.

        Non-browser local clients may omit Origin/Fetch Metadata. They still need
        the exact local Host and JSON writes. This is not local-user authentication.
        """
        port = self.server.server_port
        authorities = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            authorities.update({"127.0.0.1", "localhost"})
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0].lower() not in authorities:
            self._json({"error": "Use this app's local address and port."}, HTTPStatus.FORBIDDEN)
            return False
        authority = hosts[0].lower()
        expected = urlparse("http://" + authority)

        def same_origin(value: str, *, referrer: bool = False) -> bool:
            try:
                parsed = urlparse(value)
                return (parsed.scheme == "http" and parsed.hostname == expected.hostname
                        and (parsed.port or 80) == port and parsed.username is None
                        and parsed.password is None
                        and (referrer or not (parsed.path or parsed.params or parsed.query or parsed.fragment)))
            except ValueError:
                return False

        for header in ("Origin", "Referer", "Sec-Fetch-Site"):
            values = self.headers.get_all(header, [])
            valid = len(values) <= 1
            if values and valid:
                valid = (values[0] in {"same-origin", "none"} if header == "Sec-Fetch-Site"
                         else same_origin(values[0], referrer=header == "Referer"))
            if not valid:
                self._json({"error": "Cross-origin requests are not allowed."}, HTTPStatus.FORBIDDEN)
                return False
        if write:
            types = self.headers.get_all("Content-Type", [])
            if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "application/json":
                self._json({"error": "Send application/json."}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                return False
        return True

    def _body(self) -> dict:
        """Parse JSON request body, rejecting payloads exceeding 1MB."""
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise ValueError("Expected one valid Content-Length and no Transfer-Encoding.")
        length = int(lengths[0])
        if length > 1_000_000:
            raise ValueError("Request is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object.")
        return payload

    def _static(self, name: str) -> None:
        """Serve whitelisted static frontend files with correct MIME types."""
        allowed = {
            "/": "index.html",
            "/index.html": "index.html",
            "/app.js": "app.js",
            "/styles.css": "styles.css",
            "/vendor/daisyui.css": "vendor/daisyui.css",
        }
        filename = allowed.get(name)
        if not filename:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path = WEB_ROOT / filename
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", (mimetypes.guess_type(path.name)[0] or "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        """Dispatch incoming GET requests to API status/query endpoints or static assets."""
        if not self._local_request():
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/status":
                self._json(self.service.status())
            elif parsed.path == "/api/runs":
                query = parse_qs(parsed.query)
                limit = int(query.get("limit", ["25"])[0])
                self._json({"runs": self.service.database.recent_runs(limit)})
            elif parsed.path == "/api/summary":
                query = parse_qs(parsed.query)
                days = int(query.get("days", ["7"])[0])
                self._json(self.service.database.summary(days))
            elif parsed.path == "/api/digest/latest":
                self._json({"report": self.service.latest_digest()})
            elif parsed.path == "/api/dashboard":
                query = parse_qs(parsed.query)
                self._json(self.service.memory.dashboard(query.get("period", ["weekly"])[0], query.get("date", [None])[0]))
            elif parsed.path == "/api/memory":
                self._json({"roles": self.service.memory.roles(), "notes": self.service.memory.notes(),
                            "schedule": self.service.memory.schedule_status()})
            elif parsed.path == "/api/reviews":
                self._json({"reports": self.service.memory.report_history(), "annual": self.service.memory.annual_reviews()})
            elif parsed.path == "/api/backup":
                # SQLite's backup API includes committed WAL data in a consistent snapshot.
                with tempfile.TemporaryDirectory() as temp:
                    backup = Path(temp) / "kovaaks-agent.db"
                    with self.service.database.connect() as source:
                        destination = sqlite3.connect(backup)
                        try:
                            source.backup(destination)
                        finally:
                            destination.close()
                    body = backup.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="kovaaks-agent-backup.db"')
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            else:
                self._static(parsed.path)
        except (ValueError, OSError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        """Dispatch incoming POST actions: settings, import, API keys, connection test, or digest."""
        if not self._local_request(write=True):
            return
        parsed = urlparse(self.path)
        try:
            payload = self._body()
            if parsed.path == "/api/settings":
                result = self.service.update_settings(
                    str(payload.get("watch_path", "")),
                    str(payload.get("model", "")),
                    str(payload.get("provider", "")) or None,
                )
                self._json(result)
            elif parsed.path == "/api/import":
                results = self.service.import_now(force=bool(payload.get("force", False)))
                self._json({"imports": results, "summary": self.service.database.summary(7)})
            elif parsed.path == "/api/model/key":
                provider = str(payload.get("provider", "")) or None
                provider_id = self.service.set_session_key(str(payload.get("api_key", "")), provider)
                self._json(
                    {
                        "connected": bool(self.service.session_api_keys[provider_id]),
                        "provider": provider_id,
                    }
                )
            elif parsed.path == "/api/model/test":
                self._json({"message": self.service.test_model()})
            elif parsed.path == "/api/digest":
                self._json(self.service.create_digest(str(payload.get("period", "weekly")),
                    payload.get("date") or None, str(payload.get("role_id", "coach"))))
            elif parsed.path == "/api/memory/note":
                self._json(self.service.memory.save_note(payload))
            elif parsed.path == "/api/memory/role":
                self._json(self.service.memory.save_role(payload))
            elif parsed.path == "/api/reviews/schedule":
                if not isinstance(payload.get("enabled"), bool):
                    raise ValueError("enabled must be true or false.")
                self.service.database.set_setting("annual_review_enabled", "true" if payload["enabled"] else "false")
                self._json(self.service.memory.schedule_status())
            else:
                self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except json.JSONDecodeError:
            self._json({"error": "Invalid JSON body."}, HTTPStatus.BAD_REQUEST)
        except ModelError as exc:
            payload = {"error": str(exc)}
            if exc.diagnostics:
                payload["diagnostics"] = exc.diagnostics
            self._json(payload, HTTPStatus.BAD_REQUEST)
        except (ValueError, OSError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"error": f"Unexpected local error: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Initialize service, bind ThreadingHTTPServer, register graceful shutdown, and launch browser."""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Kovaak Agent only supports local loopback access.")
    service = AppService(ROOT)
    ApiHandler.service = service
    server = ThreadingHTTPServer((host, port), ApiHandler)
    service.start()

    def shutdown(*_args):
        service.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    url = f"http://{host}:{port}"
    print(f"Kovaak Agent is running at {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        service.stop()
        server.server_close()


def main() -> None:
    """CLI entry point parsing host, port, and browser options."""
    parser = argparse.ArgumentParser(description="Run the local Kovaak Agent proof of concept.")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    run(args.host, args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
