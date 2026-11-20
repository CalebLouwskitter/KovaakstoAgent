"""Deterministic statistical analysis and weakness diagnostic engine for Kovaak Agent.

All calculations derive from Database.normalized_runs() and TaxonomyManager:
- Rolling 7-, 30-, and 90-day scenario windows anchored cleanly to a reference date.
- PB progression, medians, trimmed means, sample std dev, CV, IQR outliers, consistency score.
- Improvement velocity (OLS slope per week + R^2), plateau detection, recent vs baseline indices.
- Accuracy-vs-speed trade-off detection, within-session progression, volume-vs-performance.
- Normalized cross-scenario category aggregation (never naively averaging raw scores).
- Automatic + manual hardware-change impact comparison.
- Nine deterministic weakness detectors with sample sizes, confidence, alternative explanations,
  and explicit epistemic boundaries distinguishing statistical evidence from video/telemetry proof.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

from .taxonomy import (
    SKILL_CATEGORIES,
    SKILL_CATEGORY_MAP,
    TaxonomyManager,
    normalize_variant_stem,
)

EPISTEMIC_CAVEAT = (
    "CSV summary metrics alone cannot prove overflicking, underflicking, shaky tracking, "
    "crosshair trajectory errors, or specific mouse-control mechanics without video or raw input telemetry."
)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def _quantile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = (len(sorted_vals) - 1) * q
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return float(sorted_vals[low])
    frac = pos - low
    return float(sorted_vals[low] * (1.0 - frac) + sorted_vals[high] * frac)


def _trimmed_mean(values: list[float], proportion: float = 0.1) -> float | None:
    if not values:
        return None
    if len(values) < 5:
        return sum(values) / len(values)
    s = sorted(values)
    k = max(1, int(len(s) * proportion))
    if 2 * k >= len(s):
        return sum(s) / len(s)
    core = s[k:-k]
    return sum(core) / len(core)


def _std_sample(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(max(0.0, variance))


def _iqr_and_outliers(values: list[float]) -> tuple[float | None, int]:
    if len(values) < 4:
        return None, 0
    s = sorted(values)
    q1 = _quantile(s, 0.25)
    q3 = _quantile(s, 0.75)
    iqr = max(0.0, q3 - q1)
    if iqr == 0.0:
        return 0.0, 0
    low_fence = q1 - 1.5 * iqr
    high_fence = q3 + 1.5 * iqr
    outliers = sum(1 for v in s if v < low_fence or v > high_fence)
    return iqr, outliers


def _ols_slope_and_r2(xs: list[float], ys: list[float]) -> tuple[float | None, float | None]:
    """Ordinary least squares slope (dy/dx) and R^2, safe against constant xs or ys."""
    n = len(xs)
    if n < 3 or len(ys) != n:
        return None, None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return None, None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    syy = sum((y - my) ** 2 for y in ys)
    if syy <= 1e-12:
        return 0.0, 1.0
    r = max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))
    return slope, r * r


def _pearson_corr(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or len(ys) != n:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 1e-12 or syy <= 1e-12:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))


def _weighted_accuracy(runs: list[dict[str, Any]]) -> float | None:
    paired_shots = 0.0
    paired_hits = 0.0
    fallback_accs: list[float] = []
    for r in runs:
        shots = r.get("shots")
        hits = r.get("hits")
        acc = r.get("accuracy")
        if shots is not None and shots > 0 and hits is not None and 0.0 <= hits <= shots:
            paired_shots += shots
            paired_hits += hits
        elif acc is not None and 0.0 <= acc <= 100.0:
            fallback_accs.append(acc)
    if paired_shots > 0:
        return paired_hits / paired_shots * 100.0
    if fallback_accs:
        return sum(fallback_accs) / len(fallback_accs)
    return None


def _consistency_score(cv_pct: float | None, iqr: float | None, median_val: float | None) -> float | None:
    if cv_pct is None:
        return None
    iqr_pct = (iqr / abs(median_val) * 100.0) if (iqr is not None and median_val and abs(median_val) > 1e-9) else cv_pct
    blended_spread = 0.65 * cv_pct + 0.35 * iqr_pct
    return round(max(0.0, min(100.0, 100.0 - blended_spread * 4.0)), 1)


def _parse_dt(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("T", " ", 1)[:19])
    except ValueError:
        return None


def _round_opt(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def _confidence_for_sample(runs: int, active_days: int) -> str:
    if runs >= 12 and active_days >= 5:
        return "high"
    if runs >= 5 and active_days >= 2:
        return "medium"
    return "low"


class PerformanceAnalytics:
    """Deterministic aim-performance analytics and diagnostic engine."""

    def __init__(self, database: Any, taxonomy: TaxonomyManager):
        self.database = database
        self.taxonomy = taxonomy

    def _window_stats(self, runs: list[dict[str, Any]], anchor_day: date, window_days: int) -> dict[str, Any]:
        cutoff = (anchor_day - timedelta(days=window_days - 1)).isoformat()
        end_bound = (anchor_day + timedelta(days=1)).isoformat()
        subset = [r for r in runs if cutoff <= r["date"] < end_bound]
        scores = [r["score"] for r in subset if r["score"] is not None]
        active_days = len({r["date"] for r in subset})
        mean_val = (sum(scores) / len(scores)) if scores else None
        med_val = _median(scores)
        best_val = max(scores) if scores else None
        std_val = _std_sample(scores)
        cv_pct = (std_val / abs(mean_val) * 100.0) if (std_val is not None and mean_val and abs(mean_val) > 1e-9) else None
        coverage = "none" if not subset else ("sparse" if len(subset) < 3 or active_days < 2 else ("moderate" if len(subset) < 8 else "strong"))
        return {
            "days": window_days,
            "runs": len(subset),
            "active_days": active_days,
            "mean": _round_opt(mean_val),
            "median": _round_opt(med_val),
            "best": _round_opt(best_val),
            "std_dev": _round_opt(std_val),
            "cv_pct": _round_opt(cv_pct),
            "weighted_accuracy": _round_opt(_weighted_accuracy(subset)),
            "coverage_status": coverage,
        }

    def analyze_scenario(
        self,
        scenario: str,
        runs: list[dict[str, Any]],
        tags: list[str],
        anchor_day: date,
        all_scenario_names: list[str],
    ) -> dict[str, Any]:
        """Compute complete statistical profile for a single named scenario."""
        scores = [r["score"] for r in runs if r["score"] is not None]
        n = len(scores)
        dates = sorted({r["date"] for r in runs})
        active_days = len(dates)
        first_dt = _parse_dt(runs[0]["observed_at"]) if runs else None
        last_dt = _parse_dt(runs[-1]["observed_at"]) if runs else None
        span_days = ((last_dt.date() - first_dt.date()).days if (first_dt and last_dt) else 0)

        mean_val = (sum(scores) / n) if n else None
        med_val = _median(scores)
        trim_val = _trimmed_mean(scores)
        std_val = _std_sample(scores)
        cv_pct = (std_val / abs(mean_val) * 100.0) if (std_val is not None and mean_val and abs(mean_val) > 1e-9) else None
        iqr_val, outlier_count = _iqr_and_outliers(scores)
        consistency = _consistency_score(cv_pct, iqr_val, med_val)

        if cv_pct is None:
            volatility = "insufficient_data"
        elif cv_pct <= 4.0:
            volatility = "low"
        elif cv_pct <= 9.0:
            volatility = "moderate"
        else:
            volatility = "high"

        # PB progression
        pb_milestones: list[dict[str, Any]] = []
        best_so_far: float | None = None
        last_pb_idx = 0
        for idx, r in enumerate(runs):
            sc = r["score"]
            if sc is None:
                continue
            if best_so_far is None or sc > best_so_far:
                delta = None if best_so_far is None else round(sc - best_so_far, 3)
                best_so_far = sc
                last_pb_idx = idx
                pb_milestones.append({
                    "observed_at": r["observed_at"],
                    "date": r["date"],
                    "score": round(sc, 2),
                    "delta": delta,
                })
        runs_since_pb = max(0, (n - 1) - last_pb_idx) if n else 0
        last_pb_day = _parse_dt(pb_milestones[-1]["observed_at"]).date() if pb_milestones and _parse_dt(pb_milestones[-1]["observed_at"]) else None
        days_since_pb = max(0, (anchor_day - last_pb_day).days) if last_pb_day else 0

        # Recent vs historical baseline
        recent_n = max(3, min(10, n // 3)) if n >= 6 else (max(1, n // 2) if n >= 2 else n)
        recent_runs = runs[-recent_n:] if recent_n else []
        baseline_runs = runs[:-recent_n] if n > recent_n else []
        recent_scores = [r["score"] for r in recent_runs if r["score"] is not None]
        baseline_scores = [r["score"] for r in baseline_runs if r["score"] is not None]
        recent_med = _median(recent_scores)
        baseline_med = _median(baseline_scores)
        delta_pct = (
            ((recent_med - baseline_med) / abs(baseline_med) * 100.0)
            if (recent_med is not None and baseline_med is not None and abs(baseline_med) > 1e-9)
            else None
        )
        baseline_index = (100.0 + delta_pct) if delta_pct is not None else 100.0
        pb_proximity = (
            (recent_med / best_so_far * 100.0)
            if (recent_med is not None and best_so_far and abs(best_so_far) > 1e-9)
            else None
        )
        recent_std = _std_sample(recent_scores)
        baseline_std = _std_sample(baseline_scores)
        recent_cv = (recent_std / (sum(recent_scores) / len(recent_scores)) * 100.0) if (recent_std is not None and sum(recent_scores) != 0) else None
        baseline_cv = (baseline_std / (sum(baseline_scores) / len(baseline_scores)) * 100.0) if (baseline_std is not None and sum(baseline_scores) != 0) else None

        # Improvement velocity (OLS slope per week)
        pct_per_week = None
        points_per_week = None
        r_squared = None
        trend_label = "insufficient_data"
        if n >= 4 and first_dt is not None:
            xs_days = [((_parse_dt(r["observed_at"]) - first_dt).total_seconds() / 86400.0) if _parse_dt(r["observed_at"]) else float(i) for i, r in enumerate(runs)]
            if max(xs_days) - min(xs_days) >= 1.0:
                slope_day, r2 = _ols_slope_and_r2(xs_days, scores)
            else:
                slope_day, r2 = None, None
            if slope_day is not None and med_val and abs(med_val) > 1e-9:
                points_per_week = slope_day * 7.0
                pct_per_week = (points_per_week / abs(med_val)) * 100.0
                r_squared = r2
                if pct_per_week >= 0.75:
                    trend_label = "improving"
                elif pct_per_week <= -0.75:
                    trend_label = "declining"
                else:
                    trend_label = "stable"
            elif delta_pct is not None:
                trend_label = "improving" if delta_pct >= 1.5 else ("declining" if delta_pct <= -1.5 else "stable")

        # Plateau detection
        is_plateau = bool(
            n >= 10
            and active_days >= 4
            and span_days >= 14
            and runs_since_pb >= 7
            and pct_per_week is not None
            and abs(pct_per_week) < 0.75
            and (delta_pct is None or delta_pct <= 0.5)
        )
        plateau_reason = (
            f"{runs_since_pb} runs over {days_since_pb}d without beating PB ({best_so_far:.1f}) and flat velocity ({pct_per_week:+.2f}%/wk)"
            if is_plateau and pct_per_week is not None and best_so_far is not None
            else None
        )

        # Accuracy vs speed trade-off
        acc_runs = [r for r in runs if r.get("accuracy") is not None]
        recent_acc = _weighted_accuracy(recent_runs)
        baseline_acc = _weighted_accuracy(baseline_runs)
        acc_delta_pp = (recent_acc - baseline_acc) if (recent_acc is not None and baseline_acc is not None) else None
        score_acc_corr = (
            _pearson_corr([r["score"] for r in acc_runs], [r["accuracy"] for r in acc_runs])
            if len(acc_runs) >= 6
            else None
        )
        tradeoff_state = "none"
        if len(acc_runs) >= 6 and delta_pct is not None and acc_delta_pp is not None:
            if delta_pct >= 1.0 and acc_delta_pp <= -2.0:
                tradeoff_state = "speed_over_precision"
            elif delta_pct <= -1.0 and acc_delta_pp >= 2.0:
                tradeoff_state = "precision_over_speed"
            elif score_acc_corr is not None and score_acc_corr <= -0.45 and acc_delta_pp <= -1.5:
                tradeoff_state = "speed_over_precision"
            elif delta_pct >= 1.0 and acc_delta_pp >= 1.0:
                tradeoff_state = "balanced_gain"

        # Duration consistency check
        durations = [r["duration_seconds"] for r in runs if r.get("duration_seconds") and r["duration_seconds"] > 0]
        med_dur = _median(durations)
        mixed_durations = False
        duration_warning = None
        if len(durations) >= 3 and med_dur and med_dur > 0:
            spread = max(durations) - min(durations)
            if spread > 0.25 * med_dur:
                mixed_durations = True
                duration_warning = f"Durations vary from {min(durations):.0f}s to {max(durations):.0f}s (median {med_dur:.0f}s); raw scores may not be strictly comparable."

        # Scenario variant siblings
        stem = normalize_variant_stem(scenario)
        variant_siblings = [
            other for other in all_scenario_names
            if other != scenario and stem and normalize_variant_stem(other) == stem
        ]

        windows = {
            "7d": self._window_stats(runs, anchor_day, 7),
            "30d": self._window_stats(runs, anchor_day, 30),
            "90d": self._window_stats(runs, anchor_day, 90),
        }

        return {
            "scenario": scenario,
            "categories": tags,
            "runs": n,
            "active_days": active_days,
            "span_days": span_days,
            "first_run": runs[0]["observed_at"] if runs else None,
            "latest_run": runs[-1]["observed_at"] if runs else None,
            "confidence": _confidence_for_sample(n, active_days),
            "score": {
                "mean": _round_opt(mean_val),
                "median": _round_opt(med_val),
                "trimmed_mean": _round_opt(trim_val),
                "best": _round_opt(best_so_far),
                "latest": _round_opt(scores[-1] if scores else None),
                "std_dev": _round_opt(std_val),
                "cv_pct": _round_opt(cv_pct),
                "iqr": _round_opt(iqr_val),
                "outlier_count": outlier_count,
            },
            "consistency_score": consistency,
            "volatility": volatility,
            "weighted_accuracy": _round_opt(_weighted_accuracy(runs)),
            "recent_vs_baseline": {
                "recent_runs_count": len(recent_runs),
                "baseline_runs_count": len(baseline_runs),
                "recent_median": _round_opt(recent_med),
                "baseline_median": _round_opt(baseline_med),
                "delta_pct": _round_opt(delta_pct),
                "baseline_index": _round_opt(baseline_index, 1),
                "pb_proximity_pct": _round_opt(pb_proximity, 1),
                "recent_cv_pct": _round_opt(recent_cv),
                "baseline_cv_pct": _round_opt(baseline_cv),
            },
            "velocity": {
                "points_per_week": _round_opt(points_per_week),
                "pct_per_week": _round_opt(pct_per_week),
                "r_squared": _round_opt(r_squared, 3),
                "trend_label": trend_label,
            },
            "plateau": {
                "is_plateau": is_plateau,
                "runs_since_pb": runs_since_pb,
                "days_since_pb": days_since_pb,
                "reason": plateau_reason,
            },
            "accuracy_speed_tradeoff": {
                "state": tradeoff_state,
                "score_acc_corr": _round_opt(score_acc_corr, 3),
                "recent_accuracy": _round_opt(recent_acc),
                "baseline_accuracy": _round_opt(baseline_acc),
                "accuracy_delta_pp": _round_opt(acc_delta_pp),
            },
            "duration": {
                "median_seconds": _round_opt(med_dur, 1),
                "mixed_durations": mixed_durations,
                "warning": duration_warning,
            },
            "pb_milestones": pb_milestones[-12:],
            "total_pbs": len(pb_milestones),
            "variant_siblings": variant_siblings,
            "windows": windows,
        }

    def group_sessions(self, runs: list[dict[str, Any]], gap_minutes: int = 45) -> list[dict[str, Any]]:
        """Group chronological runs into training sessions using an inactivity gap threshold."""
        if not runs:
            return []

        # Compute per-scenario overall medians for normalized within-session progression
        by_scen: dict[str, list[float]] = {}
        for r in runs:
            if r["score"] is not None:
                by_scen.setdefault(r["scenario"], []).append(r["score"])
        scen_med = {s: (_median(vals) or 1.0) for s, vals in by_scen.items()}

        pb_running: dict[str, float] = {}
        pb_flags: list[bool] = []
        for r in runs:
            s = r["scenario"]
            sc = r["score"]
            if sc is None:
                pb_flags.append(False)
            elif s not in pb_running:
                pb_running[s] = sc
                pb_flags.append(True)
            elif sc > pb_running[s]:
                pb_running[s] = sc
                pb_flags.append(True)
            else:
                pb_flags.append(False)

        groups: list[list[tuple[dict[str, Any], bool]]] = []
        prev_dt: datetime | None = None
        for r, is_pb in zip(runs, pb_flags):
            dt = _parse_dt(r["observed_at"])
            if not groups:
                groups.append([(r, is_pb)])
            elif dt and prev_dt and (dt - prev_dt).total_seconds() <= gap_minutes * 60:
                groups[-1].append((r, is_pb))
            elif dt and prev_dt and dt.date() == prev_dt.date() and gap_minutes <= 0:
                groups[-1].append((r, is_pb))
            else:
                groups.append([(r, is_pb)])
            if dt:
                prev_dt = dt

        sessions: list[dict[str, Any]] = []
        for idx, grp in enumerate(groups, start=1):
            items = [pair[0] for pair in grp]
            pbs = [
                {"scenario": r["scenario"], "score": round(r["score"], 2), "observed_at": r["observed_at"]}
                for r, is_pb in grp
                if is_pb and r["score"] is not None
            ]
            durations = [r["duration_seconds"] for r in items if r.get("duration_seconds")]
            total_minutes = sum(durations) / 60.0 if durations else None

            # Normalized score index per run within session
            norm_indices = []
            for r in items:
                med = scen_med.get(r["scenario"], 1.0)
                if r["score"] is not None and med and abs(med) > 1e-9:
                    norm_indices.append(r["score"] / med * 100.0)

            progression = None
            if len(norm_indices) >= 3:
                third = max(1, len(norm_indices) // 3)
                early = sum(norm_indices[:third]) / third
                mid = sum(norm_indices[third : max(third + 1, len(norm_indices) - third)]) / max(1, len(norm_indices[third : max(third + 1, len(norm_indices) - third)]))
                late = sum(norm_indices[-third:]) / third
                progression = {
                    "early_index": round(early, 1),
                    "mid_index": round(mid, 1),
                    "late_index": round(late, 1),
                    "warmup_ramp_pct": round(mid - early, 1),
                    "late_drift_pct": round(late - mid, 1),
                    "end_to_end_pct": round(late - early, 1),
                }

            hw_snap = next(
                (
                    {
                        "sensitivity": r.get("sensitivity"),
                        "sensitivity_scale": r.get("sensitivity_scale"),
                        "dpi": r.get("dpi"),
                        "fov": r.get("fov"),
                        "resolution": r.get("resolution"),
                    }
                    for r in reversed(items)
                    if r.get("sensitivity") is not None or r.get("dpi") is not None
                ),
                None,
            )

            sessions.append({
                "session_id": f"session-{idx}",
                "date": items[0]["date"],
                "start_at": items[0]["observed_at"],
                "end_at": items[-1]["observed_at"],
                "runs_count": len(items),
                "total_minutes": _round_opt(total_minutes, 1),
                "scenarios": sorted({r["scenario"] for r in items}),
                "weighted_accuracy": _round_opt(_weighted_accuracy(items)),
                "pbs_set": pbs,
                "hardware_snapshot": hw_snap,
                "within_session_progression": progression,
            })

        return sessions

    def _aggregate_within_session_progression(self, sessions: list[dict[str, Any]]) -> dict[str, Any]:
        valid = [s["within_session_progression"] for s in sessions if s.get("within_session_progression")]
        if not valid:
            return {"sessions_analyzed": 0, "avg_warmup_ramp_pct": None, "avg_late_drift_pct": None, "avg_end_to_end_pct": None, "pattern": "insufficient_data"}
        warm = sum(v["warmup_ramp_pct"] for v in valid) / len(valid)
        late = sum(v["late_drift_pct"] for v in valid) / len(valid)
        e2e = sum(v["end_to_end_pct"] for v in valid) / len(valid)
        if late <= -2.0:
            pattern = "late_session_drop"
        elif e2e >= 1.5:
            pattern = "positive_warmup_gain"
        else:
            pattern = "steady"
        return {
            "sessions_analyzed": len(valid),
            "avg_warmup_ramp_pct": round(warm, 2),
            "avg_late_drift_pct": round(late, 2),
            "avg_end_to_end_pct": round(e2e, 2),
            "pattern": pattern,
        }

    def volume_vs_performance(self, runs: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute weekly training volume alongside normalized scenario performance."""
        if not runs:
            return {"weeks": [], "correlation": None, "interpretation": "No runs available."}
        by_scen: dict[str, list[float]] = {}
        for r in runs:
            if r["score"] is not None:
                by_scen.setdefault(r["scenario"], []).append(r["score"])
        scen_med = {s: (_median(vals) or 1.0) for s, vals in by_scen.items()}

        weekly: dict[str, dict[str, Any]] = {}
        for r in runs:
            dt = _parse_dt(r["observed_at"])
            if not dt:
                continue
            wk = (dt.date() - timedelta(days=dt.date().weekday())).isoformat()
            bucket = weekly.setdefault(wk, {"week_start": wk, "runs": 0, "minutes": 0.0, "timed_runs": 0, "indices": [], "days": set()})
            bucket["runs"] += 1
            bucket["days"].add(r["date"])
            if r.get("duration_seconds"):
                bucket["minutes"] += r["duration_seconds"] / 60.0
                bucket["timed_runs"] += 1
            med = scen_med.get(r["scenario"], 1.0)
            if r["score"] is not None and med and abs(med) > 1e-9:
                bucket["indices"].append(r["score"] / med * 100.0)

        weeks = []
        for wk in sorted(weekly):
            item = weekly[wk]
            idx_mean = sum(item["indices"]) / len(item["indices"]) if item["indices"] else None
            weeks.append({
                "week_start": wk,
                "runs": item["runs"],
                "active_days": len(item["days"]),
                "minutes": round(item["minutes"], 1) if item["timed_runs"] else None,
                "mean_normalized_index": _round_opt(idx_mean, 1),
            })

        corr = None
        if len(weeks) >= 4:
            xs = [float(w["runs"]) for w in weeks if w["mean_normalized_index"] is not None]
            ys = [float(w["mean_normalized_index"]) for w in weeks if w["mean_normalized_index"] is not None]
            corr = _pearson_corr(xs, ys)

        if corr is None:
            interp = "Need at least 4 active weeks to assess volume-to-performance correlation."
        elif corr >= 0.35:
            interp = f"Higher weekly volume aligns positively with normalized scores (r={corr:.2f})."
        elif corr <= -0.35:
            interp = f"Higher weekly volume aligns negatively with normalized scores (r={corr:.2f}); shorter focused blocks may help."
        else:
            interp = f"Weekly volume shows weak linear correlation with normalized scores (r={corr:.2f})."

        return {"weeks": weeks[-16:], "correlation": _round_opt(corr, 3), "interpretation": interp}

    def category_profiles(self, scenario_analyses: list[dict[str, Any]]) -> dict[str, Any]:
        """Aggregate skill profiles per category strictly from normalized within-scenario metrics."""
        profiles: list[dict[str, Any]] = []
        for cat in SKILL_CATEGORIES:
            cat_id = cat["id"]
            members = [s for s in scenario_analyses if cat_id in s["categories"]]
            total_runs = sum(s["runs"] for s in members)
            if not members:
                profiles.append({
                    "id": cat_id,
                    "label": cat["label"],
                    "short_label": cat["short_label"],
                    "runs": 0,
                    "scenarios_count": 0,
                    "skill_index": None,
                    "pb_proximity_pct": None,
                    "consistency_score": None,
                    "velocity_pct_per_week": None,
                    "weighted_accuracy": None,
                    "plateau_count": 0,
                    "confidence": "none",
                    "top_scenarios": [],
                })
                continue

            def wavg(values_and_weights: list[tuple[float | None, int]]) -> float | None:
                valid = [(v, w) for v, w in values_and_weights if v is not None and w > 0]
                if not valid:
                    return None
                denom = sum(w for _, w in valid)
                return sum(v * w for v, w in valid) / denom if denom else None

            pb_prox = wavg([(s["recent_vs_baseline"]["pb_proximity_pct"], s["runs"]) for s in members])
            baseline_idx = wavg([(s["recent_vs_baseline"]["baseline_index"], s["runs"]) for s in members])
            consistency = wavg([(s["consistency_score"], s["runs"]) for s in members])
            vel_pct = wavg([(s["velocity"]["pct_per_week"], s["runs"]) for s in members])
            acc = wavg([(s["weighted_accuracy"], s["runs"]) for s in members])
            plateaus = sum(1 for s in members if s["plateau"]["is_plateau"])
            max_days = max((s["active_days"] for s in members), default=0)

            # Composite normalized readiness/skill score (0-100) using PB proximity + consistency + trend
            components = []
            if pb_prox is not None:
                components.append((min(100.0, max(0.0, pb_prox)), 0.55))
            if consistency is not None:
                components.append((min(100.0, max(0.0, consistency)), 0.30))
            if baseline_idx is not None:
                # 100 = parity, map [85..115] -> [50..100]
                norm_trend = min(100.0, max(0.0, 75.0 + (baseline_idx - 100.0) * 1.5))
                components.append((norm_trend, 0.15))
            skill_index = (
                sum(val * wt for val, wt in components) / sum(wt for _, wt in components)
                if components
                else None
            )

            profiles.append({
                "id": cat_id,
                "label": cat["label"],
                "short_label": cat["short_label"],
                "runs": total_runs,
                "scenarios_count": len(members),
                "skill_index": _round_opt(skill_index, 1),
                "pb_proximity_pct": _round_opt(pb_prox, 1),
                "baseline_index": _round_opt(baseline_idx, 1),
                "consistency_score": _round_opt(consistency, 1),
                "velocity_pct_per_week": _round_opt(vel_pct, 2),
                "weighted_accuracy": _round_opt(acc, 1),
                "plateau_count": plateaus,
                "confidence": _confidence_for_sample(total_runs, max_days),
                "top_scenarios": [s["scenario"] for s in sorted(members, key=lambda item: item["runs"], reverse=True)[:4]],
            })

        played = [p for p in profiles if p["runs"] > 0 and p["skill_index"] is not None]
        ranked_desc = sorted(played, key=lambda p: (p["skill_index"], p["runs"]), reverse=True)
        ranked_asc = sorted(played, key=lambda p: (p["skill_index"], -(p["consistency_score"] or 0)))
        improving = [p for p in played if (p["velocity_pct_per_week"] or 0) >= 0.75]
        improving.sort(key=lambda p: p["velocity_pct_per_week"] or 0, reverse=True)
        neglected = [p for p in profiles if p["id"] != "benchmark" and p["runs"] < 3]

        return {
            "categories": profiles,
            "strongest": ranked_desc[:2],
            "weakest": ranked_asc[:2],
            "improving": improving[:3],
            "neglected": neglected,
        }

    def detect_hardware_changes(self, runs: list[dict[str, Any]], manual_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Detect hardware/sensitivity transitions in run metadata and merge manual annotations."""
        detected: list[dict[str, Any]] = []
        prev_setup: dict[str, Any] | None = None
        for r in runs:
            sens = r.get("sensitivity")
            dpi = r.get("dpi")
            fov = r.get("fov")
            res = r.get("resolution")
            if sens is None and dpi is None and fov is None and res is None:
                continue
            current = {
                "sensitivity": sens,
                "sensitivity_scale": r.get("sensitivity_scale"),
                "dpi": dpi,
                "fov": fov,
                "resolution": res,
            }
            if prev_setup is not None:
                diffs: list[str] = []
                if sens is not None and prev_setup["sensitivity"] is not None and abs(sens - prev_setup["sensitivity"]) > 1e-6:
                    scale = current["sensitivity_scale"] or prev_setup["sensitivity_scale"] or ""
                    diffs.append(f"Sensitivity {prev_setup['sensitivity']:.2f} -> {sens:.2f}{' ' + scale if scale else ''}")
                if dpi is not None and prev_setup["dpi"] is not None and abs(dpi - prev_setup["dpi"]) > 1e-6:
                    diffs.append(f"DPI {prev_setup['dpi']:.0f} -> {dpi:.0f}")
                if fov is not None and prev_setup["fov"] is not None and abs(fov - prev_setup["fov"]) > 1e-6:
                    diffs.append(f"FOV {prev_setup['fov']:.1f} -> {fov:.1f}")
                if res is not None and prev_setup["resolution"] is not None and res != prev_setup["resolution"]:
                    diffs.append(f"Resolution {prev_setup['resolution']} -> {res}")
                if diffs:
                    detected.append({
                        "id": f"auto-{r['observed_at']}",
                        "observed_at": r["observed_at"],
                        "change_type": "sensitivity" if any("Sensitivity" in d or "DPI" in d for d in diffs) else "setup",
                        "summary": "; ".join(diffs),
                        "details": {"before": prev_setup, "after": current},
                        "source": "auto_detected",
                        "note": "",
                    })
            prev_setup = {k: (v if v is not None else (prev_setup[k] if prev_setup else None)) for k, v in current.items()}

        all_events = [dict(ev) for ev in manual_events] + detected
        all_events.sort(key=lambda e: e["observed_at"], reverse=True)

        # Compute before-vs-after normalized impact for each event
        by_scen: dict[str, list[float]] = {}
        for r in runs:
            if r["score"] is not None:
                by_scen.setdefault(r["scenario"], []).append(r["score"])
        scen_med = {s: (_median(vals) or 1.0) for s, vals in by_scen.items()}

        for ev in all_events:
            ts = ev["observed_at"].replace("T", " ", 1)[:19]
            before = [r for r in runs if r["observed_at"] < ts and r["score"] is not None]
            after = [r for r in runs if r["observed_at"] >= ts and r["score"] is not None]
            shared = {r["scenario"] for r in before} & {r["scenario"] for r in after}
            before_shared = [r for r in before if r["scenario"] in shared][-25:]
            after_shared = [r for r in after if r["scenario"] in shared][:25]
            if len(before_shared) >= 3 and len(after_shared) >= 3:
                b_idx = [r["score"] / scen_med[r["scenario"]] * 100.0 for r in before_shared if scen_med.get(r["scenario"])]
                a_idx = [r["score"] / scen_med[r["scenario"]] * 100.0 for r in after_shared if scen_med.get(r["scenario"])]
                b_med = _median(b_idx)
                a_med = _median(a_idx)
                b_acc = _weighted_accuracy(before_shared)
                a_acc = _weighted_accuracy(after_shared)
                b_std = _std_sample(b_idx)
                a_std = _std_sample(a_idx)
                ev["impact"] = {
                    "comparable": True,
                    "shared_scenarios": len(shared),
                    "before_runs": len(before_shared),
                    "after_runs": len(after_shared),
                    "before_index_median": _round_opt(b_med, 1),
                    "after_index_median": _round_opt(a_med, 1),
                    "index_delta_pct": _round_opt(((a_med - b_med) / b_med * 100.0) if (b_med and a_med is not None) else None, 2),
                    "before_accuracy": _round_opt(b_acc),
                    "after_accuracy": _round_opt(a_acc),
                    "accuracy_delta_pp": _round_opt((a_acc - b_acc) if (a_acc is not None and b_acc is not None) else None),
                    "before_std": _round_opt(b_std),
                    "after_std": _round_opt(a_std),
                }
            else:
                ev["impact"] = {
                    "comparable": False,
                    "before_runs": len(before),
                    "after_runs": len(after),
                    "reason": "Need at least 3 runs in shared scenarios both before and after the change.",
                }

        return all_events

    def diagnose_weaknesses(
        self,
        runs: list[dict[str, Any]],
        scenario_analyses: list[dict[str, Any]],
        cat_summary: dict[str, Any],
        session_prog: dict[str, Any],
        hardware_events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Identify measurable bottlenecks from observable statistical patterns without unproven physical claims."""
        diagnoses: list[dict[str, Any]] = []
        total_runs = len(runs)
        total_days = len({r["date"] for r in runs})

        if total_runs == 0:
            return []

        # 1. Speed-over-precision trade-off
        for s in scenario_analyses:
            t = s["accuracy_speed_tradeoff"]
            rvb = s["recent_vs_baseline"]
            if t["state"] == "speed_over_precision":
                cv_rose = (
                    rvb["recent_cv_pct"] is not None
                    and rvb["baseline_cv_pct"] is not None
                    and rvb["recent_cv_pct"] > rvb["baseline_cv_pct"]
                )
                diagnoses.append({
                    "id": f"diag-tradeoff-{s['scenario']}",
                    "pattern_code": "speed_over_precision_tradeoff",
                    "title": f"Speed-over-precision trade-off in {s['scenario']}",
                    "scope": s["scenario"],
                    "categories": s["categories"],
                    "severity": "high" if abs(t["accuracy_delta_pp"] or 0) >= 4.0 else "medium",
                    "confidence": s["confidence"],
                    "sample_size": s["runs"],
                    "active_days": s["active_days"],
                    "supporting_metrics": {
                        "score_delta_pct": rvb["delta_pct"],
                        "accuracy_delta_pp": t["accuracy_delta_pp"],
                        "baseline_accuracy": t["baseline_accuracy"],
                        "recent_accuracy": t["recent_accuracy"],
                        "recent_cv_pct": rvb["recent_cv_pct"],
                        "baseline_cv_pct": rvb["baseline_cv_pct"],
                        "variance_increased": cv_rose,
                    },
                    "evidence_summary": (
                        f"Median score rose {rvb['delta_pct']:+.1f}% while weighted accuracy fell "
                        f"{t['accuracy_delta_pp']:+.1f} pp ({t['baseline_accuracy']:.1f}% -> {t['recent_accuracy']:.1f}%)"
                        + (f" and CV rose from {rvb['baseline_cv_pct']:.1f}% to {rvb['recent_cv_pct']:.1f}%." if cv_rose else ".")
                    ),
                    "alternative_explanations": [
                        "Deliberately pushing click cadence or target-switch speed before hit confirmation catches up.",
                        "Warmup, fatigue, or recent sensitivity changes altering stopping control.",
                        "Scenario difficulty or firing behavior shifts (if spam-clicking rather than single-tapping).",
                    ],
                    "epistemic_note": EPISTEMIC_CAVEAT,
                    "recommended_focus": "Shift temporary priority toward precision and hit confirmation before chasing higher speed.",
                })

        # 2. High score volatility
        for s in scenario_analyses:
            if s["runs"] >= 5 and s["score"]["cv_pct"] is not None and s["score"]["cv_pct"] >= 9.0:
                diagnoses.append({
                    "id": f"diag-volatility-{s['scenario']}",
                    "pattern_code": "high_score_volatility",
                    "title": f"High run-to-run score volatility in {s['scenario']}",
                    "scope": s["scenario"],
                    "categories": s["categories"],
                    "severity": "high" if s["score"]["cv_pct"] >= 14.0 else "medium",
                    "confidence": s["confidence"],
                    "sample_size": s["runs"],
                    "active_days": s["active_days"],
                    "supporting_metrics": {
                        "cv_pct": s["score"]["cv_pct"],
                        "consistency_score": s["consistency_score"],
                        "median": s["score"]["median"],
                        "iqr": s["score"]["iqr"],
                        "outliers": s["score"]["outlier_count"],
                    },
                    "evidence_summary": (
                        f"Coefficient of variation is {s['score']['cv_pct']:.1f}% across {s['runs']} runs "
                        f"(consistency index {s['consistency_score']}/100, {s['score']['outlier_count']} IQR outlier(s))."
                    ),
                    "alternative_explanations": [
                        "Playing both cold warmup runs and peak late-session runs in the same sample.",
                        "Inconsistent target spawn RNG or uneven pacing between runs.",
                        "Mixed run durations or sensitivity experiments inside the same scenario.",
                    ],
                    "epistemic_note": EPISTEMIC_CAVEAT,
                    "recommended_focus": "Prioritize raising median floor consistency rather than hunting outlier PBs.",
                })

        # 3. Training plateau
        for s in scenario_analyses:
            if s["plateau"]["is_plateau"]:
                diagnoses.append({
                    "id": f"diag-plateau-{s['scenario']}",
                    "pattern_code": "training_plateau",
                    "title": f"Performance plateau in {s['scenario']}",
                    "scope": s["scenario"],
                    "categories": s["categories"],
                    "severity": "medium",
                    "confidence": s["confidence"],
                    "sample_size": s["runs"],
                    "active_days": s["active_days"],
                    "supporting_metrics": {
                        "runs_since_pb": s["plateau"]["runs_since_pb"],
                        "days_since_pb": s["plateau"]["days_since_pb"],
                        "pct_per_week": s["velocity"]["pct_per_week"],
                        "pb": s["score"]["best"],
                        "recent_median": s["recent_vs_baseline"]["recent_median"],
                    },
                    "evidence_summary": s["plateau"]["reason"] or "Flat trend without a recent PB.",
                    "alternative_explanations": [
                        "Repetitive practice of the exact same scenario speed/size without progressive overload.",
                        "Insufficient recovery or playing primarily while fatigued after long game sessions.",
                        "Natural diminishing returns near a high skill percentile requiring harder or slower control variants.",
                    ],
                    "epistemic_note": EPISTEMIC_CAVEAT,
                    "recommended_focus": "Introduce controlled difficulty variation (easier control drill or harder speed stimulus) rather than repeating identical runs.",
                })

        # 4. Late-session fatigue drop
        if session_prog.get("sessions_analyzed", 0) >= 3 and session_prog.get("pattern") == "late_session_drop":
            diagnoses.append({
                "id": "diag-session-drop",
                "pattern_code": "late_session_fatigue_drop",
                "title": "Within-session late-run performance decline",
                "scope": "multi_session",
                "categories": [],
                "severity": "medium",
                "confidence": "medium" if session_prog["sessions_analyzed"] < 6 else "high",
                "sample_size": session_prog["sessions_analyzed"],
                "active_days": total_days,
                "supporting_metrics": {
                    "sessions_analyzed": session_prog["sessions_analyzed"],
                    "avg_late_drift_pct": session_prog["avg_late_drift_pct"],
                    "avg_warmup_ramp_pct": session_prog["avg_warmup_ramp_pct"],
                },
                "evidence_summary": (
                    f"Across {session_prog['sessions_analyzed']} multi-run sessions, late-session normalized scores "
                    f"averaged {session_prog['avg_late_drift_pct']:+.1f}% vs mid-session peak."
                ),
                "alternative_explanations": [
                    "Saving harder benchmark or fatigue-inducing scenarios for the end of a session.",
                    "Concentration, visual fatigue, or wrist fatigue accumulating late in longer sessions.",
                    "Testing different settings or winding down without full focus.",
                ],
                "epistemic_note": EPISTEMIC_CAVEAT,
                "recommended_focus": "Schedule priority benchmarks after a short warmup and cap high-intensity blocks before drop-off.",
            })

        # 5. Category imbalance / weakest category
        played_cats = [c for c in cat_summary["categories"] if c["runs"] >= 4 and c["id"] != "benchmark" and c["skill_index"] is not None]
        if len(played_cats) >= 2:
            best_c = max(played_cats, key=lambda c: c["skill_index"])
            weak_c = min(played_cats, key=lambda c: c["skill_index"])
            gap = best_c["skill_index"] - weak_c["skill_index"]
            if gap >= 6.0 or weak_c["skill_index"] < 75.0:
                diagnoses.append({
                    "id": f"diag-catlag-{weak_c['id']}",
                    "pattern_code": "category_imbalance_or_lag",
                    "title": f"{weak_c['label']} lags strongest skill area",
                    "scope": weak_c["label"],
                    "categories": [weak_c["id"]],
                    "severity": "medium" if gap < 12.0 else "high",
                    "confidence": weak_c["confidence"],
                    "sample_size": weak_c["runs"],
                    "active_days": total_days,
                    "supporting_metrics": {
                        "weak_category": weak_c["label"],
                        "weak_skill_index": weak_c["skill_index"],
                        "weak_pb_proximity_pct": weak_c["pb_proximity_pct"],
                        "strong_category": best_c["label"],
                        "strong_skill_index": best_c["skill_index"],
                        "gap_points": round(gap, 1),
                    },
                    "evidence_summary": (
                        f"{weak_c['label']} normalized index ({weak_c['skill_index']:.1f}) trails "
                        f"{best_c['label']} ({best_c['skill_index']:.1f}) by {gap:.1f} points across {weak_c['runs']} runs."
                    ),
                    "alternative_explanations": [
                        "Less consistent practice cadence or fewer warmup runs before attempting scenarios in this category.",
                        "Playing disproportionately harder scenario variants in this category than in others.",
                        "Natural differences in scenario scoring sensitivity across tracking vs clicking drills.",
                    ],
                    "epistemic_note": EPISTEMIC_CAVEAT,
                    "recommended_focus": f"Allocate focused early-session volume to {weak_c['label']} fundamentals.",
                })

        # 6. Neglected core fundamentals (only if player has meaningful total volume)
        if total_runs >= 12 and cat_summary["neglected"]:
            neglected_names = [c["label"] for c in cat_summary["neglected"][:3]]
            diagnoses.append({
                "id": "diag-neglected-categories",
                "pattern_code": "neglected_fundamental",
                "title": "Under-represented skill categories in recent training",
                "scope": "training_balance",
                "categories": [c["id"] for c in cat_summary["neglected"][:3]],
                "severity": "low",
                "confidence": "high" if total_runs >= 25 else "medium",
                "sample_size": total_runs,
                "active_days": total_days,
                "supporting_metrics": {
                    "neglected_categories": neglected_names,
                    "total_runs": total_runs,
                },
                "evidence_summary": f"Fewer than 3 runs recorded in: {', '.join(neglected_names)} across {total_runs} total runs.",
                "alternative_explanations": [
                    "Intentional specialization around a specific game or benchmark focus.",
                    "Sessions played outside KovaaK's or without stats export enabled.",
                ],
                "epistemic_note": EPISTEMIC_CAVEAT,
                "recommended_focus": "Include balanced rotating exposure if aiming for all-around mouse control.",
            })

        # 7. Post-hardware adaptation dip
        for ev in hardware_events[:3]:
            imp = ev.get("impact") or {}
            if imp.get("comparable") and imp.get("index_delta_pct") is not None and imp["index_delta_pct"] <= -2.5:
                diagnoses.append({
                    "id": f"diag-hw-{ev['id']}",
                    "pattern_code": "post_hardware_adaptation",
                    "title": f"Post-change score dip after {ev['summary']}",
                    "scope": ev["summary"],
                    "categories": [],
                    "severity": "medium",
                    "confidence": "medium" if imp["after_runs"] < 10 else "high",
                    "sample_size": imp["before_runs"] + imp["after_runs"],
                    "active_days": total_days,
                    "supporting_metrics": imp,
                    "evidence_summary": (
                        f"Normalized scores averaged {imp['index_delta_pct']:+.1f}% across {imp['after_runs']} post-change runs "
                        f"vs {imp['before_runs']} prior runs."
                    ),
                    "alternative_explanations": [
                        "Normal neuromuscular adaptation window after changing sensitivity, DPI, or gear.",
                        "Fatigue or scenario selection differences after the change.",
                    ],
                    "epistemic_note": EPISTEMIC_CAVEAT,
                    "recommended_focus": "Hold setup stable for 10–14 days and rebuild baseline smoothness before further tweaks.",
                })

        # 8. Inconsistent run durations
        mixed = [s for s in scenario_analyses if s["duration"]["mixed_durations"]]
        if mixed:
            names = ", ".join(s["scenario"] for s in mixed[:3])
            diagnoses.append({
                "id": "diag-mixed-duration",
                "pattern_code": "inconsistent_run_duration",
                "title": "Mixed run durations complicate score trends",
                "scope": names,
                "categories": [],
                "severity": "low",
                "confidence": "high",
                "sample_size": sum(s["runs"] for s in mixed),
                "active_days": total_days,
                "supporting_metrics": {"scenarios": [s["scenario"] for s in mixed[:5]]},
                "evidence_summary": f"Duration spreads exceed 25% of median in: {names}.",
                "alternative_explanations": [
                    "Mixing Free Play / aborted runs with standard 60s Challenge runs.",
                    "Scenario version or custom duration edits.",
                ],
                "epistemic_note": EPISTEMIC_CAVEAT,
                "recommended_focus": "Separate standard Challenge runs from Free Play runs when comparing scores.",
            })

        # 9. Sparse data coverage (important for honest uncertainty)
        if total_runs < 10 or total_days < 4:
            diagnoses.append({
                "id": "diag-sparse-coverage",
                "pattern_code": "sparse_data_coverage",
                "title": "Limited historical sample size — treat trends as preliminary",
                "scope": "overall_history",
                "categories": [],
                "severity": "low",
                "confidence": "low",
                "sample_size": total_runs,
                "active_days": total_days,
                "supporting_metrics": {"total_runs": total_runs, "active_days": total_days},
                "evidence_summary": f"Only {total_runs} run(s) across {total_days} active day(s) are currently retained.",
                "alternative_explanations": [
                    "New installation or partial folder import.",
                    "Normal early-baseline variance before steady habits form.",
                ],
                "epistemic_note": EPISTEMIC_CAVEAT,
                "recommended_focus": "Record 3–5 sessions across core scenarios to establish reliable baselines.",
            })

        sev_rank = {"high": 0, "medium": 1, "low": 2}
        conf_rank = {"high": 0, "medium": 1, "low": 2}
        diagnoses.sort(key=lambda d: (sev_rank.get(d["severity"], 3), conf_rank.get(d["confidence"], 3), -d["sample_size"]))
        return diagnoses

    def scenario_trend(self, scenario: str, days: int = 90, anchor: str | None = None) -> dict[str, Any]:
        """Return chronological trend series, rolling median, PB flags, and hardware markers for one scenario."""
        clean = (scenario or "").strip()
        runs = self.database.normalized_runs(scenario=clean)
        if not runs:
            return {"scenario": clean, "points": [], "hardware_events": [], "analysis": None}

        latest_day = _parse_dt(runs[-1]["observed_at"]).date() if _parse_dt(runs[-1]["observed_at"]) else date.today()
        anchor_day = date.fromisoformat(anchor[:10]) if anchor else latest_day
        if days > 0:
            cutoff = (anchor_day - timedelta(days=days - 1)).isoformat()
            end_bound = (anchor_day + timedelta(days=1)).isoformat()
            filtered = [r for r in runs if cutoff <= r["date"] < end_bound]
        else:
            filtered = list(runs)

        if not filtered:
            filtered = list(runs)

        best_so_far: float | None = None
        points: list[dict[str, Any]] = []
        window_scores: list[float] = []
        for r in filtered:
            sc = r["score"]
            if sc is None:
                continue
            window_scores.append(sc)
            recent_slice = window_scores[-5:]
            is_pb = best_so_far is None or sc > best_so_far
            if is_pb:
                best_so_far = sc
            points.append({
                "observed_at": r["observed_at"],
                "date": r["date"],
                "score": round(sc, 2),
                "rolling_median": _round_opt(_median(recent_slice)),
                "accuracy": _round_opt(r.get("accuracy")),
                "duration_seconds": r.get("duration_seconds"),
                "is_pb": is_pb,
                "sensitivity": r.get("sensitivity"),
                "dpi": r.get("dpi"),
            })

        all_names = sorted({r["scenario"] for r in self.database.normalized_runs()})
        tags = self.taxonomy.get_tags(clean)["categories"]
        analysis = self.analyze_scenario(clean, runs, tags, anchor_day, all_names)

        # Relevant hardware events inside date span
        min_date = filtered[0]["date"]
        max_date = filtered[-1]["date"]
        with self.database.connect() as conn:
            manual_hw = [dict(row) for row in conn.execute("SELECT * FROM hardware_events ORDER BY observed_at DESC").fetchall()]
        hw_events = [
            ev for ev in self.detect_hardware_changes(runs, manual_hw)
            if min_date <= ev["observed_at"][:10] <= max_date
        ]

        return {
            "scenario": clean,
            "days": days,
            "points": points,
            "hardware_events": hw_events,
            "analysis": analysis,
        }

    def full_report(self, days: int = 90, anchor: str | None = None) -> dict[str, Any]:
        """Build complete deterministic performance intelligence report across all retained runs."""
        all_runs = self.database.normalized_runs()
        if not all_runs:
            return {
                "empty": True,
                "total_runs": 0,
                "active_days": 0,
                "anchor_date": anchor or date.today().isoformat(),
                "rolling_windows": {"7d": {"runs": 0}, "30d": {"runs": 0}, "90d": {"runs": 0}},
                "scenarios": [],
                "category_summary": self.category_profiles([]),
                "diagnoses": [],
                "sessions": [],
                "session_progression": {"sessions_analyzed": 0, "pattern": "insufficient_data"},
                "volume_vs_performance": {"weeks": [], "correlation": None, "interpretation": "Import training runs to begin analysis."},
                "hardware_events": [],
                "five_questions": {
                    "improving": "No runs imported yet.",
                    "holding_back": "Import your KovaaK's stats folder or load the bundled sample history to diagnose bottlenecks.",
                    "train_today": "Load your history or sample first to generate a personalized routine.",
                    "measurable_progress": "No baseline runs yet.",
                    "next_session_change": "Import stats and record a readiness check-in.",
                },
                "epistemic_caveat": EPISTEMIC_CAVEAT,
            }

        latest_dt = _parse_dt(all_runs[-1]["observed_at"])
        anchor_day = date.fromisoformat(anchor[:10]) if anchor else (latest_dt.date() if latest_dt else date.today())

        if days > 0:
            cutoff = (anchor_day - timedelta(days=days - 1)).isoformat()
            end_bound = (anchor_day + timedelta(days=1)).isoformat()
            window_runs = [r for r in all_runs if cutoff <= r["date"] < end_bound]
            if not window_runs:
                window_runs = list(all_runs)
        else:
            window_runs = list(all_runs)

        by_scenario: dict[str, list[dict[str, Any]]] = {}
        for r in window_runs:
            by_scenario.setdefault(r["scenario"], []).append(r)

        all_names = sorted(by_scenario.keys())
        tag_map = self.taxonomy.all_scenario_tags(all_names)

        scenario_analyses = [
            self.analyze_scenario(name, by_scenario[name], tag_map[name]["categories"], anchor_day, all_names)
            for name in all_names
        ]
        scenario_analyses.sort(key=lambda s: (-s["runs"], s["scenario"].lower()))

        cat_summary = self.category_profiles(scenario_analyses)
        sessions = self.group_sessions(window_runs)
        session_prog = self._aggregate_within_session_progression(sessions)
        vol_perf = self.volume_vs_performance(window_runs)

        with self.database.connect() as conn:
            manual_hw = [dict(row) for row in conn.execute("SELECT * FROM hardware_events ORDER BY observed_at DESC").fetchall()]
            for item in manual_hw:
                import json
                item["details"] = json.loads(item.pop("details_json", "{}") or "{}")
        hardware_events = self.detect_hardware_changes(all_runs, manual_hw)

        diagnoses = self.diagnose_weaknesses(window_runs, scenario_analyses, cat_summary, session_prog, hardware_events)

        rolling = {
            "7d": self._overall_window(all_runs, anchor_day, 7),
            "30d": self._overall_window(all_runs, anchor_day, 30),
            "90d": self._overall_window(all_runs, anchor_day, 90),
        }

        improving_scenarios = [
            s for s in scenario_analyses if (s["velocity"]["pct_per_week"] or 0) >= 0.75 and s["runs"] >= 4
        ]
        improving_scenarios.sort(key=lambda s: s["velocity"]["pct_per_week"] or 0, reverse=True)
        top_diag = diagnoses[0] if diagnoses else None

        five_questions = {
            "improving": (
                ", ".join(f"{s['scenario']} ({s['velocity']['pct_per_week']:+.1f}%/wk)" for s in improving_scenarios[:3])
                if improving_scenarios
                else ("Strongest recent trend: " + cat_summary["improving"][0]["label"] if cat_summary["improving"] else "Collecting enough repeated runs to confirm multi-week upward slopes.")
            ),
            "holding_back": (
                f"{top_diag['title']} — {top_diag['evidence_summary']}"
                if top_diag
                else "No severe statistical bottlenecks detected in the current sample."
            ),
            "train_today": (
                f"Prioritize {top_diag['recommended_focus']}"
                if top_diag
                else (f"Focus on {cat_summary['weakest'][0]['label']}" if cat_summary["weakest"] else "Balanced foundational routine")
            ),
            "measurable_progress": (
                f"{rolling['30d']['runs']} runs across {rolling['30d']['active_days']} active day(s) in the 30d window; "
                f"{sum(s['total_pbs'] for s in scenario_analyses)} PB milestone(s) captured."
            ),
            "next_session_change": (
                top_diag["recommended_focus"]
                if top_diag
                else "Maintain progressive scenario difficulty and log a post-session evaluation."
            ),
        }

        return {
            "empty": False,
            "total_runs": len(window_runs),
            "lifetime_runs": len(all_runs),
            "active_days": len({r["date"] for r in window_runs}),
            "first_run": all_runs[0]["observed_at"],
            "latest_run": all_runs[-1]["observed_at"],
            "anchor_date": anchor_day.isoformat(),
            "rolling_windows": rolling,
            "scenarios": scenario_analyses,
            "scenario_tags": tag_map,
            "category_summary": cat_summary,
            "diagnoses": diagnoses,
            "sessions": list(reversed(sessions[-30:])),
            "session_progression": session_prog,
            "volume_vs_performance": vol_perf,
            "hardware_events": hardware_events[:20],
            "five_questions": five_questions,
            "epistemic_caveat": EPISTEMIC_CAVEAT,
        }

    def _overall_window(self, runs: list[dict[str, Any]], anchor_day: date, window_days: int) -> dict[str, Any]:
        cutoff = (anchor_day - timedelta(days=window_days - 1)).isoformat()
        end_bound = (anchor_day + timedelta(days=1)).isoformat()
        subset = [r for r in runs if cutoff <= r["date"] < end_bound]
        timed = [r["duration_seconds"] for r in subset if r.get("duration_seconds")]
        return {
            "days": window_days,
            "runs": len(subset),
            "scenarios": len({r["scenario"] for r in subset}),
            "active_days": len({r["date"] for r in subset}),
            "minutes": _round_opt(sum(timed) / 60.0 if timed else None, 1),
            "weighted_accuracy": _round_opt(_weighted_accuracy(subset)),
        }
