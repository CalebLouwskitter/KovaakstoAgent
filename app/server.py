"""Embedded HTTP server and REST API for Kovaak Agent.

Exposes local REST endpoints:
- GET  /api/status        -> Application, watcher, and 7-day performance status
- GET  /api/runs          -> Recent normalized runs (limit parameter)
- GET  /api/summary       -> Rolling aggregate metrics (days parameter)
- GET  /api/digest/latest -> Most recent cached coaching digest
- GET  /api/records       -> Personal bests and practice streaks (period, date parameters)
- POST /api/settings      -> Update watch path, model, or provider
- POST /api/import        -> Trigger manual on-demand CSV scan
- POST /api/model/key     -> Store session API key in memory
- POST /api/model/test    -> Run connection test with active provider
- POST /api/digest        -> Generate a selected-period AI coaching report
- POST /api/detect        -> Find KovaaK's stats folders in Steam libraries
- POST /api/desktop/autostart -> Toggle start-with-Windows (desktop app only)

Serves static UI files (/index.html, /app.js, /styles.css, /vendor/daisyui.css, /favicon.ico) from the 'web' folder.
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
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from .exporter import export_analysis_csv, export_analysis_json
from .importer import inspect_unsupported_files
from .model import ModelError
from .paths import legacy_data_dir, migrate_legacy_data, resource_root, user_data_dir
from .service import AppService
from .steam import find_stats_folders


ROOT = resource_root()
WEB_ROOT = ROOT / "web"


class DesktopIntegration(Protocol):
    """Hooks the desktop shell provides; absent when serving a regular browser."""

    def autostart_available(self) -> bool: ...

    def autostart_enabled(self) -> bool: ...

    def set_autostart(self, enabled: bool) -> None: ...


class ApiHandler(BaseHTTPRequestHandler):
    """HTTP request handler routing REST API calls and static file requests."""
    service: AppService
    desktop: DesktopIntegration | None = None

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

    def _reject(self, message: str, status: HTTPStatus) -> None:
        """Refuse a request without acting on it, then close the connection.

        A small declared body is read and discarded first: closing a socket with
        unread bytes makes Windows reset it, so the client would never see the refusal.
        """
        lengths = self.headers.get_all("Content-Length", [])
        if (len(lengths) == 1 and lengths[0].isascii() and lengths[0].isdigit()
                and int(lengths[0]) <= 1_000_000 and not self.headers.get_all("Transfer-Encoding")):
            self.rfile.read(int(lengths[0]))
        self.close_connection = True
        self._json({"error": message}, status)

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
            self._reject("Use this app's local address and port.", HTTPStatus.FORBIDDEN)
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
                self._reject("Cross-origin requests are not allowed.", HTTPStatus.FORBIDDEN)
                return False
        if write:
            types = self.headers.get_all("Content-Type", [])
            if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "application/json":
                self._reject("Send application/json.", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
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
            "/charts.js": "charts.js",
            "/styles.css": "styles.css",
            "/vendor/daisyui.css": "vendor/daisyui.css",
            "/favicon.ico": "favicon.ico",
        }
        filename = allowed.get(name)
        if not filename:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path = WEB_ROOT / filename
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type.endswith("javascript"):
            content_type += "; charset=utf-8"
        self.send_header("Content-Type", content_type)
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
                status = self.service.status()
                if self.desktop:
                    status["desktop"] = {"autostart_available": self.desktop.autostart_available(),
                                         "autostart": self.desktop.autostart_enabled()}
                self._json(status)
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
            elif parsed.path == "/api/records":
                query = parse_qs(parsed.query)
                self._json(self.service.records(query.get("period", ["week"])[0], query.get("date", [None])[0]))
            elif parsed.path == "/api/memory":
                self._json({"roles": self.service.memory.roles(), "notes": self.service.memory.notes(),
                            "schedule": self.service.memory.schedule_status()})
            elif parsed.path == "/api/reviews":
                self._json({"reports": self.service.memory.report_history(), "annual": self.service.memory.annual_reviews()})
            elif parsed.path == "/api/intelligence":
                query = parse_qs(parsed.query)
                days = int(query.get("days", ["90"])[0])
                anchor = query.get("date", [None])[0]
                self._json(self.service.analytics.full_report(days=days, anchor=anchor))
            elif parsed.path == "/api/scenario/trend":
                query = parse_qs(parsed.query)
                scenario = query.get("scenario", [""])[0]
                if not scenario.strip():
                    raise ValueError("Specify a scenario parameter.")
                days = int(query.get("days", ["90"])[0])
                anchor = query.get("date", [None])[0]
                self._json(self.service.analytics.scenario_trend(scenario, days=days, anchor=anchor))
            elif parsed.path == "/api/taxonomy":
                runs = self.service.database.normalized_runs()
                scenarios = sorted({r["scenario"] for r in runs if r.get("scenario")})
                self._json({
                    "categories": self.service.taxonomy.categories(),
                    "catalog": self.service.taxonomy.catalog(),
                    "tags": self.service.taxonomy.all_scenario_tags(scenarios),
                })
            elif parsed.path == "/api/coach/brief":
                query = parse_qs(parsed.query)
                period = query.get("period", ["weekly"])[0]
                anchor = query.get("date", [None])[0]
                self._json({"brief": self.service.coaching.latest_brief(period=period, anchor=anchor)})
            elif parsed.path == "/api/coach/effectiveness":
                self._json({"outcomes": self.service.coaching.evaluate_recommendations()})
            elif parsed.path == "/api/plans/today":
                query = parse_qs(parsed.query)
                plan_date = query.get("date", [None])[0]
                self._json({
                    "plan": self.service.planner.get_active_plan(plan_date),
                    "modes": self.service.planner.modes(),
                    "readiness": self.service.memory.latest_readiness(plan_date),
                })
            elif parsed.path == "/api/plans":
                query = parse_qs(parsed.query)
                limit = int(query.get("limit", ["20"])[0])
                self._json({"plans": self.service.planner.list_plans(limit)})
            elif parsed.path == "/api/readiness":
                query = parse_qs(parsed.query)
                limit = int(query.get("limit", ["30"])[0])
                self._json({"checkins": self.service.memory.list_readiness_checkins(limit)})
            elif parsed.path == "/api/history/timeline":
                query = parse_qs(parsed.query)
                days = int(query.get("days", ["90"])[0])
                limit = int(query.get("limit", ["30"])[0])
                anchor = query.get("date", [None])[0]
                report = self.service.analytics.full_report(days=days, anchor=anchor)
                self._json({
                    "sessions": report["sessions"][:limit],
                    "session_progression": report["session_progression"],
                    "hardware_events": report["hardware_events"],
                    "volume_vs_performance": report["volume_vs_performance"],
                })
            elif parsed.path == "/api/hardware":
                report = self.service.analytics.full_report(days=0)
                self._json({"events": report["hardware_events"]})
            elif parsed.path == "/api/benchmarks":
                self._json(self.service.benchmarks.evaluate_all())
            elif parsed.path == "/api/export":
                query = parse_qs(parsed.query)
                fmt = query.get("format", ["json"])[0].strip().lower()
                days = int(query.get("days", ["90"])[0])
                anchor = query.get("date", [None])[0]
                if fmt == "csv":
                    content = export_analysis_csv(self.service.analytics, days=days, anchor=anchor).encode("utf-8")
                    ctype = "text/csv; charset=utf-8"
                    fname = "kovaaks-analysis.csv"
                elif fmt == "json":
                    content = export_analysis_json(self.service.analytics, days=days, anchor=anchor).encode("utf-8")
                    ctype = "application/json; charset=utf-8"
                    fname = "kovaaks-analysis.json"
                else:
                    raise ValueError("Unsupported export format. Choose json or csv.")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)
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
                local_ep = payload.get("local_endpoint")
                result = self.service.update_settings(
                    str(payload.get("watch_path", "")),
                    str(payload.get("model", "")),
                    str(payload.get("provider", "")) or None,
                    str(local_ep) if local_ep is not None else None,
                )
                self._json(result)
            elif parsed.path == "/api/import":
                data = self.service.import_with_summary(force=bool(payload.get("force", False)))
                perf_info = inspect_unsupported_files(self.service.watch_path)
                data["report_summary"]["unsupported_perf_message"] = perf_info["message"]
                self._json({
                    "imports": data["imports"],
                    "report_summary": data["report_summary"],
                    "summary": self.service.database.summary(7),
                })
            elif parsed.path == "/api/sample/load":
                self._json(self.service.load_sample())
            elif parsed.path == "/api/taxonomy/tag":
                scenario = str(payload.get("scenario", ""))
                if payload.get("reset"):
                    self._json(self.service.taxonomy.reset_tags(scenario))
                else:
                    cats = payload.get("categories")
                    if not isinstance(cats, list):
                        raise ValueError("categories must be a list of category IDs.")
                    self._json(self.service.taxonomy.set_manual_tags(scenario, cats))
            elif parsed.path == "/api/coach/generate":
                use_llm = bool(payload.get("use_llm", False))
                fallback = bool(payload.get("fallback_on_error", True))
                self._json(
                    self.service.generate_coaching_brief(
                        period=str(payload.get("period", "weekly")),
                        anchor=payload.get("date") or None,
                        role_id=str(payload.get("role_id", "coach")),
                        use_llm=use_llm,
                        fallback_on_error=fallback,
                    )
                )
            elif parsed.path == "/api/coach/ask":
                self._json(
                    self.service.ask_coach(
                        str(payload.get("question", "")),
                        anchor=payload.get("date") or None,
                        use_llm=bool(payload.get("use_llm", False)),
                    )
                )
            elif parsed.path == "/api/plans/generate":
                readiness_payload = payload.get("readiness")
                if isinstance(readiness_payload, dict):
                    readiness_payload = self.service.memory.save_readiness_checkin(readiness_payload)
                plan = self.service.planner.generate_plan(
                    mode=str(payload.get("mode", "balanced_30")),
                    plan_date=payload.get("date") or None,
                    readiness=readiness_payload if isinstance(readiness_payload, dict) else None,
                    target_minutes=int(payload["target_minutes"]) if payload.get("target_minutes") is not None else None,
                )
                self._json({"plan": plan})
            elif parsed.path == "/api/plans/evaluate":
                plan_id = str(payload.get("plan_id", ""))
                if not plan_id.strip():
                    raise ValueError("Specify plan_id.")
                plan = self.service.planner.evaluate_plan(
                    plan_id,
                    subjective_feedback=str(payload.get("subjective_feedback", "")),
                )
                self._json({"plan": plan})
            elif parsed.path == "/api/readiness":
                checkin = self.service.memory.save_readiness_checkin(payload)
                self._json({"checkin": checkin})
            elif parsed.path == "/api/hardware":
                event = self.service.memory.save_hardware_event(payload)
                self._json({"event": event})
            elif parsed.path == "/api/hardware/delete":
                self._json({"events": self.service.memory.delete_hardware_event(str(payload.get("id", "")))})
            elif parsed.path == "/api/benchmarks":
                self._json(self.service.benchmarks.save_definition(payload))
            elif parsed.path == "/api/benchmarks/delete":
                self._json(self.service.benchmarks.delete_definition(str(payload.get("id", ""))))
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
            elif parsed.path == "/api/memory/note/delete":
                self._json(self.service.memory.delete_note(str(payload.get("id", ""))))
            elif parsed.path == "/api/memory/role":
                self._json(self.service.memory.save_role(payload))
            elif parsed.path == "/api/detect":
                self._json({"folders": [str(folder) for folder in find_stats_folders()]})
            elif parsed.path == "/api/desktop/autostart":
                if not self.desktop:
                    raise ValueError("Start with Windows is available in the desktop app.")
                if not isinstance(payload.get("enabled"), bool):
                    raise ValueError("enabled must be true or false.")
                self.desktop.set_autostart(payload["enabled"])
                self._json({"autostart": self.desktop.autostart_enabled()})
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


def create_server(host: str = "127.0.0.1", port: int = 8765,
                  data_dir: str | Path | None = None) -> tuple[ThreadingHTTPServer, AppService]:
    """Prepare user data, build the service and bind the local HTTP server (not yet serving).

    Port 0 binds any free port; read the chosen one from server.server_port.
    """
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Kovaak Agent only supports local loopback access.")
    data_path = Path(data_dir).expanduser() if data_dir else user_data_dir()
    migrated = migrate_legacy_data(legacy_data_dir(), data_path)
    if migrated:
        print(f"Copied your existing data from {migrated} to {data_path}. The original file was left in place.")
    service = AppService(ROOT, data_path)
    detected = service.autodetect_watch_path()
    if detected:
        print(f"Found KovaaK's stats folder: {detected}")
    ApiHandler.service = service
    server = ThreadingHTTPServer((host, port), ApiHandler)
    return server, service


def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True,
        data_dir: str | Path | None = None) -> None:
    """Initialize service, bind ThreadingHTTPServer, register graceful shutdown, and launch browser."""
    server, service = create_server(host, port, data_dir)
    service.start()

    def shutdown(*_args):
        service.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    url = f"http://{host}:{server.server_port}"
    print(f"Kovaak Agent is running at {url}")
    print(f"Data folder: {service.data_dir}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        service.stop()
        server.server_close()


def main() -> None:
    """CLI entry point parsing host, port, data folder, and browser options."""
    parser = argparse.ArgumentParser(description="Run Kovaak Agent in your browser.")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", help="Folder for the database (default: %%APPDATA%%\\Kovaak Agent).")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    run(args.host, args.port, open_browser=not args.no_browser, data_dir=args.data_dir)


if __name__ == "__main__":
    main()
