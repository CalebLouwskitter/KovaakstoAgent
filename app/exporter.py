"""CSV and JSON export of aggregated deterministic performance intelligence."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from .analytics import PerformanceAnalytics


def export_analysis_json(analytics: PerformanceAnalytics, days: int = 90, anchor: str | None = None) -> str:
    """Serialize aggregated intelligence report to indented UTF-8 JSON."""
    report = analytics.full_report(days=days, anchor=anchor)
    return json.dumps(report, ensure_ascii=False, indent=2)


def export_analysis_csv(analytics: PerformanceAnalytics, days: int = 90, anchor: str | None = None) -> str:
    """Serialize per-scenario aggregated intelligence metrics to CSV."""
    report = analytics.full_report(days=days, anchor=anchor)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Scenario",
        "Categories",
        "Runs",
        "Active Days",
        "Mean Score",
        "Median Score",
        "Best Score (PB)",
        "Std Dev",
        "CV (%)",
        "Consistency (0-100)",
        "Weighted Accuracy (%)",
        "Recent Median",
        "Baseline Median",
        "Baseline Delta (%)",
        "PB Proximity (%)",
        "Velocity (%/wk)",
        "Trend",
        "Plateau",
        "Accuracy-Speed Tradeoff",
        "Confidence",
    ])
    for s in report.get("scenarios", []):
        writer.writerow([
            s["scenario"],
            "; ".join(s["categories"]),
            s["runs"],
            s["active_days"],
            s["score"]["mean"] if s["score"]["mean"] is not None else "",
            s["score"]["median"] if s["score"]["median"] is not None else "",
            s["score"]["best"] if s["score"]["best"] is not None else "",
            s["score"]["std_dev"] if s["score"]["std_dev"] is not None else "",
            s["score"]["cv_pct"] if s["score"]["cv_pct"] is not None else "",
            s["consistency_score"] if s["consistency_score"] is not None else "",
            s["weighted_accuracy"] if s["weighted_accuracy"] is not None else "",
            s["recent_vs_baseline"]["recent_median"] if s["recent_vs_baseline"]["recent_median"] is not None else "",
            s["recent_vs_baseline"]["baseline_median"] if s["recent_vs_baseline"]["baseline_median"] is not None else "",
            s["recent_vs_baseline"]["delta_pct"] if s["recent_vs_baseline"]["delta_pct"] is not None else "",
            s["recent_vs_baseline"]["pb_proximity_pct"] if s["recent_vs_baseline"]["pb_proximity_pct"] is not None else "",
            s["velocity"]["pct_per_week"] if s["velocity"]["pct_per_week"] is not None else "",
            s["velocity"]["trend_label"],
            "yes" if s["plateau"]["is_plateau"] else "no",
            s["accuracy_speed_tradeoff"]["state"],
            s["confidence"],
        ])
    return buf.getvalue()
