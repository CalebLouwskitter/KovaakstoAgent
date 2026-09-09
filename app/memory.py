"""Provider-independent coaching memory and deterministic calendar reviews.

Raw captures are durable; model context is deliberately bounded and includes
coverage/truncation counts. Model prose is always labeled as prior advice.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import date, datetime, timedelta


PERIODS = ("daily", "weekly", "monthly", "yearly")
DEFAULT_ROLES = (
    ("coach", "Long-term coach", "Build sustainable practice around the player's goals. Follow up on prior advice."),
    ("analyst", "Performance analyst", "Focus on scenario-specific trends, sample sizes, consistency and evidence gaps."),
    ("planner", "Practice planner", "Turn measured weaknesses and the player's available time into two measurable practice priorities."),
)
FACT_FIELDS = ("observed_at", "scenario", "score", "accuracy", "duration_seconds", "shots", "hits",
               "misses", "sensitivity", "sensitivity_scale", "dpi", "fov", "avg_fps", "weapon")


def period_bounds(period: str, anchor: str | None = None):
    if period not in PERIODS:
        raise ValueError("Choose daily, weekly, monthly, or yearly.")
    day = date.fromisoformat(anchor) if anchor else date.today()
    if not 1971 <= day.year <= 9998:
        raise ValueError("Review dates must be between 1971 and 9998.")
    if period == "daily":
        start, end = day, day + timedelta(days=1)
        previous = start - timedelta(days=1)
    elif period == "weekly":
        start = day - timedelta(days=day.weekday())
        end, previous = start + timedelta(days=7), start - timedelta(days=7)
    elif period == "monthly":
        start = day.replace(day=1)
        end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        previous = (start - timedelta(days=1)).replace(day=1)
    else:
        start, end, previous = date(day.year, 1, 1), date(day.year + 1, 1, 1), date(day.year - 1, 1, 1)
    return start.isoformat(), end.isoformat(), previous.isoformat()


class CoachingMemory:
    def __init__(self, database):
        self.database = database
        self._stop = threading.Event()
        self._thread = None
        self.last_error = None
        with database.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS agent_roles (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, instructions TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS coaching_notes (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL,
                    scenario TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS annual_reviews (
                    year INTEGER PRIMARY KEY, body TEXT NOT NULL, evidence_json TEXT NOT NULL,
                    source_revision INTEGER NOT NULL DEFAULT -1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            """)
            if "source_revision" not in {row["name"] for row in connection.execute("PRAGMA table_info(annual_reviews)")}:
                connection.execute("ALTER TABLE annual_reviews ADD COLUMN source_revision INTEGER NOT NULL DEFAULT -1")
            for role in DEFAULT_ROLES:
                connection.execute("INSERT OR IGNORE INTO agent_roles VALUES(?,?,?)", role)
            fields = ",".join(f"json_extract(payload_json,'$.{field}') AS {field}"
                              for field in FACT_FIELDS if field not in ("observed_at", "scenario"))
            connection.execute(f"""CREATE VIEW IF NOT EXISTS history_metrics AS
                SELECT event_key, observed_at, scenario, {fields}
                FROM run_history""")

    def roles(self):
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM agent_roles ORDER BY name")]

    def role(self, role_id):
        for role in self.roles():
            if role["id"] == role_id:
                return role
        raise ValueError("Choose a saved agent role.")

    def save_role(self, payload):
        name = str(payload.get("name", "")).strip()
        instructions = str(payload.get("instructions", "")).strip()
        role_id = str(payload.get("id") or uuid.uuid4().hex)
        if not name or len(name) > 80 or not instructions or len(instructions) > 2000:
            raise ValueError("Roles need a name (up to 80 characters) and instructions (up to 2,000 characters).")
        with self.database.connect() as connection:
            connection.execute("""INSERT INTO agent_roles VALUES(?,?,?) ON CONFLICT(id)
                DO UPDATE SET name=excluded.name, instructions=excluded.instructions""", (role_id, name, instructions))
        return self.role(role_id)

    def notes(self):
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM coaching_notes ORDER BY updated_at DESC, id")]

    def save_note(self, payload):
        kind = str(payload.get("kind", "note"))
        status = str(payload.get("status", "active"))
        body = str(payload.get("body", "")).strip()
        scenario = str(payload.get("scenario", "")).strip()
        if kind not in ("goal", "preference", "constraint", "note", "outcome") or status not in ("active", "completed", "archived"):
            raise ValueError("Invalid memory type or status.")
        if not body or len(body) > 2000 or len(scenario) > 200:
            raise ValueError("Write a memory of 1–2,000 characters and an optional scenario of up to 200 characters.")
        note_id = str(payload.get("id") or uuid.uuid4().hex)
        with self.database.connect() as connection:
            connection.execute("""INSERT INTO coaching_notes(id,kind,body,scenario,status) VALUES(?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,body=excluded.body,
                scenario=excluded.scenario,status=excluded.status,updated_at=CURRENT_TIMESTAMP""",
                (note_id, kind, body, scenario, status))
        return {"notes": self.notes()}

    def _aggregate(self, connection, start, end, group=""):
        # Only paired shot/hit records contribute to weighted accuracy.
        query = """COUNT(*) AS runs, COUNT(DISTINCT date(observed_at)) AS active_days,
            SUM(duration_seconds) / 60.0 AS minutes,
            AVG(score) AS average_score, MAX(score) AS best_score,
            COALESCE(SUM(CASE WHEN shots > 0 AND hits BETWEEN 0 AND shots THEN hits END) * 100.0 /
              NULLIF(SUM(CASE WHEN shots > 0 AND hits BETWEEN 0 AND shots THEN shots END),0),
              AVG(CASE WHEN accuracy BETWEEN 0 AND 100 THEN accuracy END)) AS average_accuracy,
            SUM(CASE WHEN shots > 0 AND hits BETWEEN 0 AND shots THEN 1 ELSE 0 END) AS paired_accuracy_runs,
            SUM(CASE WHEN score IS NOT NULL THEN 1 ELSE 0 END) AS scored_runs,
            SUM(CASE WHEN duration_seconds IS NOT NULL THEN 1 ELSE 0 END) AS timed_runs"""
        prefix = "scenario," if group else ""
        suffix = " GROUP BY scenario ORDER BY runs DESC, scenario" if group else ""
        rows = connection.execute(f"SELECT {prefix}{query} FROM history_metrics WHERE observed_at >= ? AND observed_at < ?{suffix}",
                                  (start, end)).fetchall()
        result = [dict(row) for row in rows]
        return result if group else result[0]

    def dashboard(self, period="weekly", anchor=None):
        start, end, previous = period_bounds(period, anchor)
        with self.database.connect() as connection:
            summary = self._aggregate(connection, start, end)
            prior = self._aggregate(connection, previous, start)
            scenarios = self._aggregate(connection, start, end, "scenario")
            prior_scenarios = {item["scenario"]: item for item in self._aggregate(connection, previous, start, "scenario")}
            for item in scenarios:
                baseline = prior_scenarios.get(item["scenario"])
                item["previous_runs"] = baseline["runs"] if baseline else 0
                item["score_change_percent"] = ((item["average_score"] / baseline["average_score"] - 1) * 100
                    if baseline and baseline["average_score"] and item["average_score"] is not None else None)
            bucket = "%Y-%m" if period == "yearly" else "%Y-%m-%d"
            timeline = [dict(row) for row in connection.execute("""SELECT strftime(?,observed_at) AS bucket,
                COUNT(*) AS runs, SUM(duration_seconds)/60.0 AS minutes FROM history_metrics
                WHERE observed_at >= ? AND observed_at < ? GROUP BY bucket ORDER BY bucket""", (bucket, start, end))]
            lifetime = dict(connection.execute("SELECT COUNT(*) AS runs, MIN(observed_at) AS first_run, MAX(observed_at) AS latest_run FROM history_metrics").fetchone())
        if summary["runs"]:
            buckets = {item["bucket"]: item for item in timeline}
            timeline = []
            cursor = date.fromisoformat(start)
            while cursor.isoformat() < end:
                label = cursor.strftime(bucket)
                timeline.append(buckets.get(label, {"bucket": label, "runs": 0, "minutes": None}))
                cursor = (date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
                          if period == "yearly" else cursor + timedelta(days=1))
        # Cross-scenario score averages must never be presented as a progression metric.
        for totals in (summary, prior):
            totals.pop("average_score")
            totals.pop("best_score")
        return {"period": period, "start": start, "end_exclusive": end, "previous_start": previous,
                "partial": start <= date.today().isoformat() < end,
                "calendar": "Computer local time; Monday-start weeks. Dates without a timezone use the export's wall clock.",
                "summary": summary, "previous": prior, "scenarios": scenarios, "timeline": timeline,
                "lifetime": lifetime, "schedule": self.schedule_status()}

    def report_history(self, limit=30):
        with self.database.connect() as connection:
            rows = connection.execute("SELECT id,kind,provider,model,body,evidence_json,created_at FROM reports ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        results = []
        for row in rows:
            report = dict(row)
            evidence = json.loads(report.pop("evidence_json"))
            report.update(period=evidence.get("period", "weekly"), start=evidence.get("start"),
                          role=evidence.get("role", {}).get("name", "Aim analyst"))
            results.append(report)
        return results

    def evidence(self, period="weekly", anchor=None, role_id="coach"):
        dashboard = self.dashboard(period, anchor)
        role = self.role(role_id)
        relevant_names = {item["scenario"] for item in dashboard["scenarios"]}
        notes = [item for item in self.notes() if item["status"] != "archived"
                 and (not item["scenario"] or item["scenario"] in relevant_names
                      or (item["status"] == "active" and item["kind"] in ("goal", "constraint", "preference")))]
        notes.sort(key=lambda item: (item["status"] != "active", item["kind"] not in ("goal", "constraint", "preference")))
        with self.database.connect() as connection:
            all_time = self._aggregate(connection, "0001-01-01", dashboard["end_exclusive"], "scenario")
            lifetime = dict(connection.execute("SELECT COUNT(*) AS runs, MIN(observed_at) AS first_run, MAX(observed_at) AS latest_run FROM history_metrics WHERE observed_at < ?",
                                               (dashboard["end_exclusive"],)).fetchone())
            recent = [dict(row) for row in connection.execute("SELECT * FROM history_metrics WHERE observed_at >= ? AND observed_at < ? ORDER BY observed_at DESC LIMIT 20",
                                                           (dashboard["start"], dashboard["end_exclusive"]))]
        for run in recent:
            run.pop("event_key", None)  # Includes a local source path; never send it upstream.
        selected_names = {item["scenario"] for item in dashboard["scenarios"][:40]}
        history = [item for item in all_time if item["scenario"] in selected_names]
        reports = self.report_history(5)
        advice = [{"id": item["id"], "created_at": item["created_at"], "role": item["role"],
                   "provider": item["provider"], "body": item["body"][:3000],
                   "truncated": len(item["body"]) > 3000} for item in reports]
        return {"schema_version": 1, "period": period, "start": dashboard["start"],
                "end_exclusive": dashboard["end_exclusive"], "partial": dashboard["partial"],
                "calendar": dashboard["calendar"], "summary": dashboard["summary"],
                "previous_period": dashboard["previous"], "scenarios": dashboard["scenarios"][:40],
                "timeline": dashboard["timeline"], "lifetime": lifetime,
                "lifetime_scope": "Retained history up to the selected period's end. Player notes and prior advice are current context, with their own dates.",
                "lifetime_scenarios": history, "role": role, "player_memory": notes[:24],
                "prior_advice_not_verified_facts": advice, "recent_runs": recent,
                "coverage": {"scenarios_included": min(40, len(dashboard["scenarios"])),
                             "scenarios_total": len(dashboard["scenarios"]), "memories_included": min(24, len(notes)),
                             "memories_relevant": len(notes), "recent_runs_limit": 20, "prior_reports_limit": 5},
                "limitations": ["No external benchmarks or evidence of following a plan unless the player records an outcome.",
                                "Partial periods are compared with the full prior period; volume is not directly comparable.",
                                "Unknown timestamps use file modification time; renamed/copied exports can duplicate runs."]}

    def schedule_status(self):
        return {"enabled": self.database.get_setting("annual_review_enabled", "true") == "true",
                "next_due": f"{date.today().year + 1}-01-01T00:00:00",
                "timing": "At year close (January 1, 00:00, computer local time). Catches up on next app launch.",
                "mode": "Local statistical review; no API key or model charge required.", "error": self.last_error}

    def annual_reviews(self):
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT year,body,created_at FROM annual_reviews ORDER BY year DESC")]

    def run_due_reviews(self, now=None):
        if self.database.get_setting("annual_review_enabled", "true") != "true":
            return []
        now = now or datetime.now()
        with self.database.connect() as connection:
            years = [int(row[0]) for row in connection.execute("SELECT DISTINCT substr(observed_at,1,4) FROM run_history WHERE substr(observed_at,1,4) < ?", (str(now.year),))
                     if row[0] and str(row[0]).isdigit() and 1971 <= int(row[0]) <= 9998]
        saved = []
        for year in sorted(years):
            with self.database.connect() as connection:
                revision = connection.execute("SELECT revision FROM history_versions WHERE year=?", (year,)).fetchone()[0]
                if connection.execute("SELECT 1 FROM annual_reviews WHERE year=? AND source_revision=?", (year, revision)).fetchone():
                    continue
            evidence = self.dashboard("yearly", f"{year}-01-01")
            summary = evidence["summary"]
            lines = [f"### {year} year in review", f"{summary['runs']} retained runs across {summary['active_days']} active days.",
                     "#### Scenario results"]
            for item in evidence["scenarios"]:
                score = "unavailable" if item["best_score"] is None else f"{item['best_score']:.2f}"
                lines.append(f"- {item['scenario']}: {item['runs']} runs; best score {score}.")
            lines += ["#### Evidence limitations", "This is a saved statistical snapshot of captured runs. Unimported sessions are not included. Generate a coaching report for interpretation."]
            with self.database.connect() as connection:
                cursor = connection.execute("""INSERT INTO annual_reviews(year,body,evidence_json,source_revision) VALUES(?,?,?,?)
                    ON CONFLICT(year) DO UPDATE SET body=excluded.body,evidence_json=excluded.evidence_json,
                    source_revision=excluded.source_revision,created_at=CURRENT_TIMESTAMP
                    WHERE annual_reviews.source_revision < excluded.source_revision""",
                                            (year, "\n\n".join(lines), json.dumps(evidence), revision))
                if cursor.rowcount:
                    saved.append(year)
        return saved

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="annual-review", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.run_due_reviews()
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)
            self._stop.wait(60)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
