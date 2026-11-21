"""Benchmark tracking and user-imported community threshold definitions.

Avoids fabricating unverified external Voltaic/Viscose rank thresholds:
- Always tracks personal baseline benchmarks across scenarios tagged `benchmark`.
- Allows users to import verified JSON benchmark definitions (e.g., Voltaic S4/S5 or Viscose)
  and evaluates all-time PBs and 30-day medians against those imported thresholds.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from .analytics import PerformanceAnalytics


BENCHMARK_TEMPLATE: dict[str, Any] = {
    "name": "Custom Aim Benchmark",
    "version": "1.0",
    "ranks": ["Bronze", "Silver", "Gold", "Platinum", "Diamond"],
    "scenarios": {
        "Pasu Voltaic Easy": {
            "Bronze": 450,
            "Silver": 520,
            "Gold": 590,
            "Platinum": 660,
            "Diamond": 730,
        },
        "Smoothbot Voltaic Easy": {
            "Bronze": 2400,
            "Silver": 2800,
            "Gold": 3200,
            "Platinum": 3600,
            "Diamond": 4000,
        },
    },
}


class BenchmarkManager:
    """Manages personal benchmark summaries and custom user-imported rank tables."""

    def __init__(self, database: Any, analytics: PerformanceAnalytics):
        self.database = database
        self.analytics = analytics

    def list_definitions(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, name, version, source, definitions_json, created_at FROM custom_benchmarks ORDER BY created_at DESC"
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["definition"] = json.loads(item.pop("definitions_json"))
            results.append(item)
        return results

    def save_definition(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and store a custom JSON benchmark threshold table."""
        if not isinstance(payload, dict):
            raise ValueError("Benchmark payload must be a JSON object.")
        name = str(payload.get("name") or "").strip()
        version = str(payload.get("version") or "1.0").strip()
        ranks = payload.get("ranks")
        scenarios = payload.get("scenarios")
        if not name or len(name) > 120:
            raise ValueError("Benchmark requires a name (1-120 characters).")
        if not isinstance(ranks, list) or not ranks or not all(isinstance(r, str) and r.strip() for r in ranks):
            raise ValueError("Benchmark requires a non-empty list of rank names.")
        clean_ranks = [r.strip() for r in ranks]
        if not isinstance(scenarios, dict) or not scenarios:
            raise ValueError("Benchmark requires a non-empty scenarios threshold mapping.")

        clean_scenarios: dict[str, dict[str, float]] = {}
        for scen_name, thresholds in scenarios.items():
            s_key = str(scen_name).strip()
            if not s_key or not isinstance(thresholds, dict):
                raise ValueError(f"Invalid threshold entry for scenario '{scen_name}'.")
            parsed_thresholds: dict[str, float] = {}
            for r_name in clean_ranks:
                if r_name in thresholds and thresholds[r_name] is not None:
                    try:
                        parsed_thresholds[r_name] = float(thresholds[r_name])
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"Non-numeric threshold for {s_key} / {r_name}.") from exc
            if not parsed_thresholds:
                raise ValueError(f"Scenario '{s_key}' has no numeric rank thresholds.")
            clean_scenarios[s_key] = parsed_thresholds

        definition = {"name": name, "version": version, "ranks": clean_ranks, "scenarios": clean_scenarios}
        bench_id = str(payload.get("id") or uuid.uuid4().hex)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO custom_benchmarks(id, name, version, source, definitions_json)
                VALUES(?, ?, ?, 'user', ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, version=excluded.version, definitions_json=excluded.definitions_json
                """,
                (bench_id, name, version, json.dumps(definition, ensure_ascii=False)),
            )
        return self.evaluate_all()

    def delete_definition(self, bench_id: str) -> dict[str, Any]:
        clean = str(bench_id or "").strip()
        if not clean:
            raise ValueError("Specify a benchmark id to delete.")
        with self.database.connect() as connection:
            cursor = connection.execute("DELETE FROM custom_benchmarks WHERE id = ?", (clean,))
            if cursor.rowcount == 0:
                raise ValueError("Benchmark definition not found.")
        return self.evaluate_all()

    def evaluate_all(self) -> dict[str, Any]:
        """Evaluate personal benchmark scenarios plus any user-imported rank benchmarks."""
        report = self.analytics.full_report(days=0)
        by_scen_lower = {s["scenario"].lower(): s for s in report.get("scenarios", [])}

        personal_benchmarks = []
        for s in report.get("scenarios", []):
            if "benchmark" in s.get("categories", []):
                personal_benchmarks.append({
                    "scenario": s["scenario"],
                    "categories": [c for c in s["categories"] if c != "benchmark"],
                    "runs": s["runs"],
                    "pb": s["score"]["best"],
                    "recent_median": s["recent_vs_baseline"]["recent_median"],
                    "pb_proximity_pct": s["recent_vs_baseline"]["pb_proximity_pct"],
                    "velocity_pct_per_week": s["velocity"]["pct_per_week"],
                    "total_pbs": s["total_pbs"],
                    "latest_run": s["latest_run"],
                })

        custom_evaluations = []
        for entry in self.list_definitions():
            defn = entry["definition"]
            ranks = defn["ranks"]
            scenario_rows = []
            rank_indices: list[int] = []
            for scen_name, thresholds in defn["scenarios"].items():
                stats = by_scen_lower.get(scen_name.lower())
                pb = stats["score"]["best"] if stats else None
                achieved_rank = None
                achieved_idx = -1
                next_rank = ranks[0] if ranks else None
                next_threshold = thresholds.get(next_rank) if next_rank else None

                if pb is not None:
                    for idx, rank_name in enumerate(ranks):
                        req = thresholds.get(rank_name)
                        if req is not None and pb >= req:
                            achieved_rank = rank_name
                            achieved_idx = idx
                        elif req is not None:
                            next_rank = rank_name
                            next_threshold = req
                            break
                    else:
                        next_rank = None
                        next_threshold = None

                if achieved_idx >= 0:
                    rank_indices.append(achieved_idx)

                pct_to_next = None
                if pb is not None and next_threshold and next_threshold > 0:
                    pct_to_next = round(min(100.0, pb / next_threshold * 100.0), 1)

                scenario_rows.append({
                    "scenario": scen_name,
                    "runs": stats["runs"] if stats else 0,
                    "pb": pb,
                    "recent_median": stats["recent_vs_baseline"]["recent_median"] if stats else None,
                    "achieved_rank": achieved_rank or "Unranked",
                    "next_rank": next_rank,
                    "next_threshold": next_threshold,
                    "progress_to_next_pct": pct_to_next,
                })

            overall_rank = ranks[min(rank_indices)] if len(rank_indices) == len(defn["scenarios"]) and rank_indices else "Incomplete"
            custom_evaluations.append({
                "id": entry["id"],
                "name": entry["name"],
                "version": entry["version"],
                "ranks": ranks,
                "overall_complete_rank": overall_rank,
                "scenarios": scenario_rows,
            })

        return {
            "personal_benchmarks": personal_benchmarks,
            "custom_benchmarks": custom_evaluations,
            "template": BENCHMARK_TEMPLATE,
            "note": (
                "Kovaak Agent avoids hardcoding unverified community rank tables. "
                "Personal benchmark scenarios are tracked automatically, and you can import verified JSON rank tables."
            ),
        }
