"""Adaptive training planner and closed-loop post-session evaluator.

Generates evidence-grounded daily routines across seven modes, adapts difficulty
with explainable rules, scales or advises rest based on self-reported fatigue and
discomfort, and closes the feedback loop by evaluating completed runs against targets.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

from .analytics import PerformanceAnalytics, _median, _weighted_accuracy
from .memory import CoachingMemory
from .taxonomy import (
    CATALOG_BY_NAME_LOWER,
    SKILL_CATEGORY_MAP,
    TaxonomyManager,
    VERIFIED_SCENARIO_CATALOG,
)


MEDICAL_DISCLAIMER = (
    "Kovaak Agent cannot diagnose injuries or provide medical advice. "
    "Never grind through pain; stop training and consult a qualified healthcare professional if discomfort persists."
)

ROUTINE_MODES: tuple[dict[str, Any], ...] = (
    {
        "id": "warmup_10",
        "label": "10-Minute Warm-Up",
        "default_minutes": 10,
        "item_count": 3,
        "description": "Low-fatigue activation covering smoothness, click timing, and quick target transitions.",
    },
    {
        "id": "focused_20",
        "label": "20-Minute Focused Training",
        "default_minutes": 20,
        "item_count": 3,
        "description": "Targets your primary measurable bottleneck with crisp difficulty guardrails.",
    },
    {
        "id": "balanced_30",
        "label": "30-Minute Balanced Training",
        "default_minutes": 30,
        "item_count": 4,
        "description": "Balances clicking, target switching, and tracking with priority weight on lagging skills.",
    },
    {
        "id": "development_50",
        "label": "45–60 Min Development Session",
        "default_minutes": 50,
        "item_count": 6,
        "description": "Full progressive block combining activation, primary weakness development, secondary skills, and closure.",
    },
    {
        "id": "benchmark",
        "label": "Benchmark Assessment",
        "default_minutes": 30,
        "item_count": 4,
        "description": "Structured benchmark check across your benchmark-tagged or core community benchmark scenarios.",
    },
    {
        "id": "recovery",
        "label": "Low-Intensity Recovery Day",
        "default_minutes": 15,
        "item_count": 3,
        "description": "Low-tension smoothness and relaxed control drills designed to keep feel sharp without strain.",
    },
    {
        "id": "cs2_prep",
        "label": "CS2 Aim Preparation",
        "default_minutes": 25,
        "item_count": 4,
        "description": "Emphasizes horizontal micro-clicking, crisp stopping control, multi-target switching, and level tracking.",
    },
)
ROUTINE_MODE_MAP: dict[str, dict[str, Any]] = {item["id"]: item for item in ROUTINE_MODES}


def compute_difficulty_decision(scenario_stats: dict[str, Any] | None, catalog_entry: Any = None) -> dict[str, Any]:
    """Choose an explainable adaptive difficulty decision from verified scenario stats."""
    if not scenario_stats or scenario_stats.get("runs", 0) < 3:
        return {
            "action": "establish_baseline",
            "label": "Establish Baseline",
            "reason": "Fewer than 3 retained runs on record; complete clean reps to establish a reliable median.",
            "progress_if": "Complete 5+ runs with CV <= 8% and stable or improving accuracy.",
            "regress_if": "Scores vary widely (>12% CV) or tension/fatigue interferes with control.",
        }

    runs = scenario_stats["runs"]
    rvb = scenario_stats["recent_vs_baseline"]
    vel = scenario_stats["velocity"]
    plateau = scenario_stats["plateau"]
    tradeoff = scenario_stats["accuracy_speed_tradeoff"]
    cv = scenario_stats["score"].get("cv_pct")
    consistency = scenario_stats.get("consistency_score")
    pb_prox = rvb.get("pb_proximity_pct")
    cats = scenario_stats.get("categories", [])

    if tradeoff.get("state") == "speed_over_precision":
        return {
            "action": "shift_speed_to_precision",
            "label": "Shift Speed -> Precision",
            "reason": f"Score rose while accuracy dropped {tradeoff.get('accuracy_delta_pp')} pp; rebuild clean hit confirmation.",
            "progress_if": "Maintain current median score while recovering weighted accuracy within 2 pp of baseline.",
            "regress_if": "Accuracy keeps falling below 80% (clicking) or CV rises above 12% -> drop one difficulty step.",
        }

    if (cv is not None and cv >= 11.0) or (consistency is not None and consistency < 58.0):
        easier = catalog_entry.easier_variant if catalog_entry else None
        return {
            "action": "reduce_prioritize_control",
            "label": "Reduce Difficulty / Control",
            "reason": f"Run-to-run volatility is high (CV {cv}%, consistency {consistency}/100)." + (f" Consider stepping down to {easier}." if easier else ""),
            "progress_if": "Bring 5-run CV below 8% and raise consistency score above 70/100.",
            "regress_if": "Continue struggling with erratic outliers -> switch to an easier/larger target variant.",
        }

    if "benchmark" in cats and (plateau.get("days_since_pb", 0) >= 10 or runs >= 8) and (pb_prox is not None and pb_prox >= 93.0):
        return {
            "action": "repeat_benchmark",
            "label": "Repeat Benchmark",
            "reason": f"Recent median sits at {pb_prox}% of PB across {runs} runs; ready for a fresh benchmark push after warmup.",
            "progress_if": "Set a new PB or beat recent median on 2 consecutive benchmark runs.",
            "regress_if": "Median drops >3% below baseline -> return to foundational training variants for 3 sessions.",
        }

    if plateau.get("is_plateau"):
        harder = catalog_entry.harder_variant if catalog_entry else None
        return {
            "action": "progress_harder" if harder else "prioritize_consistency",
            "label": "Break Plateau via Stimulus Shift" if harder else "Prioritize Consistency",
            "reason": plateau.get("reason") or "Performance has flattened across recent sessions.",
            "progress_if": f"Test {harder} for 2-3 runs then return to beat PB." if harder else "Raise recent median within 2% of PB.",
            "regress_if": "If accuracy collapses on harder stimulus, drop to an easier control drill for 1 session.",
        }

    if (
        runs >= 6
        and pb_prox is not None
        and pb_prox >= 96.5
        and (consistency is None or consistency >= 72.0)
        and (vel.get("pct_per_week") or 0) >= 0.0
    ):
        harder = catalog_entry.harder_variant if catalog_entry else None
        return {
            "action": "progress_harder",
            "label": "Progress to Harder Variant",
            "reason": f"Strong control near ceiling (median {pb_prox}% of PB, consistency {consistency}/100)." + (f" Ready for {harder}." if harder else ""),
            "progress_if": "Hold >=90% of current median score when stepping up speed or smaller targets.",
            "regress_if": "If median drops >10% or accuracy falls >5 pp on the harder drill, keep this drill as main work.",
        }

    if cv is not None and cv >= 8.0:
        return {
            "action": "prioritize_consistency",
            "label": "Prioritize Consistency",
            "reason": f"Moderate spread (CV {cv}%) suggests raising the floor before chasing peak speed.",
            "progress_if": "Log 3 consecutive runs within 3% of your recent median.",
            "regress_if": "Spread widens past 11% CV -> slow down cadence and focus on smooth pathing.",
        }

    return {
        "action": "maintain_current",
        "label": "Maintain Current Difficulty",
        "reason": "Steady progress and healthy variance; continue progressive reps.",
        "progress_if": "Reach >=96.5% PB proximity with consistency >=72/100.",
        "regress_if": "Accuracy falls >3 pp or CV exceeds 11% -> prioritize control.",
    }


class TrainingPlanner:
    """Generates, tracks, and evaluates adaptive daily training routines."""

    def __init__(self, database: Any, analytics: PerformanceAnalytics, taxonomy: TaxonomyManager, memory: CoachingMemory):
        self.database = database
        self.analytics = analytics
        self.taxonomy = taxonomy
        self.memory = memory

    @staticmethod
    def modes() -> list[dict[str, Any]]:
        return [dict(m) for m in ROUTINE_MODES]

    def _assess_readiness(self, readiness: dict[str, Any] | None, requested_mode: str, target_minutes: int | None) -> dict[str, Any]:
        """Interpret readiness & fatigue metrics safely without medical diagnosis."""
        if not readiness:
            mode_meta = ROUTINE_MODE_MAP.get(requested_mode, ROUTINE_MODE_MAP["balanced_30"])
            mins = target_minutes or mode_meta["default_minutes"]
            return {
                "has_checkin": False,
                "intensity": "low" if requested_mode in ("warmup_10", "recovery") else "normal",
                "rest_advisory": False,
                "effective_mode": requested_mode,
                "effective_minutes": mins,
                "adjustments": [],
                "medical_disclaimer": MEDICAL_DISCLAIMER,
            }

        wrist = int(readiness.get("wrist_discomfort", 1))
        eye = int(readiness.get("eye_strain", 1))
        fatigue = int(readiness.get("fatigue", 2))
        energy = int(readiness.get("energy", 3))
        focus = int(readiness.get("focus", 3))
        avail = readiness.get("available_minutes")

        mode_meta = ROUTINE_MODE_MAP.get(requested_mode, ROUTINE_MODE_MAP["balanced_30"])
        effective_mode = requested_mode
        effective_minutes = target_minutes or avail or mode_meta["default_minutes"]
        if avail and target_minutes is None:
            effective_minutes = min(effective_minutes, int(avail))

        adjustments: list[str] = []
        if wrist >= 4:
            return {
                "has_checkin": True,
                "checkin": readiness,
                "intensity": "rest_recommended",
                "rest_advisory": True,
                "effective_mode": "recovery",
                "effective_minutes": 0,
                "adjustments": [
                    f"High self-reported wrist/hand discomfort ({wrist}/5). Active aim training is paused to avoid aggravating strain.",
                    "Take a full rest day, review posture/ergonomics, or watch VODs/demo reviews instead of grinding mouse input.",
                ],
                "medical_disclaimer": MEDICAL_DISCLAIMER,
            }

        intensity = "normal"
        if wrist == 3 or eye >= 4 or fatigue >= 4 or energy <= 2:
            intensity = "low_recovery"
            if requested_mode not in ("warmup_10", "recovery"):
                effective_mode = "recovery"
                adjustments.append(
                    f"Scaled routine down to Low-Intensity Recovery due to readiness check-in (fatigue {fatigue}/5, eye strain {eye}/5, wrist {wrist}/5, energy {energy}/5)."
                )
            effective_minutes = min(effective_minutes, 15 if wrist == 3 else 20)
        elif focus <= 2 or fatigue == 3 or eye == 3:
            intensity = "moderate_capped"
            effective_minutes = min(effective_minutes, 25)
            adjustments.append("Capped session length to <=25 min with shorter sets due to moderate fatigue/focus.")

        if requested_mode in ("warmup_10", "recovery") and intensity == "normal":
            intensity = "low"

        return {
            "has_checkin": True,
            "checkin": readiness,
            "intensity": intensity,
            "rest_advisory": False,
            "effective_mode": effective_mode,
            "effective_minutes": max(5, int(effective_minutes)),
            "adjustments": adjustments,
            "medical_disclaimer": MEDICAL_DISCLAIMER,
        }

    def _build_target_string(self, stats: dict[str, Any] | None, decision: dict[str, Any]) -> dict[str, Any]:
        if not stats or stats.get("runs", 0) == 0:
            return {
                "summary": "Establish a clean 3-run baseline median and record shot accuracy.",
                "target_score": None,
                "target_accuracy": None,
                "baseline_median": None,
                "pb": None,
            }
        med = stats["recent_vs_baseline"]["recent_median"] or stats["score"]["median"]
        pb = stats["score"]["best"]
        acc = stats["weighted_accuracy"]
        action = decision["action"]

        if action == "shift_speed_to_precision":
            target_score = round(med * 0.99, 1) if med else None
            target_acc = round(min(98.0, (acc or 80.0) + 2.5), 1) if acc is not None else None
            summary = f"Hold >={target_score} score while lifting accuracy toward {target_acc}%+" if (target_score and target_acc) else "Prioritize clean accuracy over raw speed."
        elif action in ("reduce_prioritize_control", "prioritize_consistency"):
            target_score = round(med, 1) if med else None
            target_acc = round(min(98.0, (acc or 75.0) + 1.0), 1) if acc is not None else None
            summary = f"Hit >={target_score} consistently across every run (keep spread tight)" + (f" with >={target_acc}% accuracy" if target_acc else "")
        elif action == "repeat_benchmark":
            target_score = round(min(pb * 1.01, med * 1.03), 1) if (pb and med) else (round(pb, 1) if pb else None)
            target_acc = round(acc, 1) if acc is not None else None
            summary = f"Challenge your PB ({pb}) — aim for >={target_score}" if (pb and target_score) else "Benchmark PB attempt."
        else:
            target_score = round(med * 1.015, 1) if med else None
            target_acc = round(acc, 1) if acc is not None else None
            summary = f"Beat recent median ({med}) -> target {target_score}+" + (f" at >={target_acc}% accuracy" if target_acc else "")

        return {
            "summary": summary,
            "target_score": target_score,
            "target_accuracy": target_acc,
            "baseline_median": med,
            "pb": pb,
        }

    def _select_candidates(
        self,
        mode: str,
        report: dict[str, Any],
        readiness_state: dict[str, Any],
        target_count: int,
    ) -> list[tuple[str, str, str, dict[str, Any] | None]]:
        """Return list of (scenario_name, source, role_objective, scenario_stats_or_none)."""
        played = report.get("scenarios", [])
        played_by_lower = {s["scenario"].lower(): s for s in played}
        cat_summary = report.get("category_summary", {})
        weak_cats = [c["id"] for c in cat_summary.get("weakest", [])]
        diagnoses = report.get("diagnoses", [])
        diag_scenarios = [
            d["scope"] for d in diagnoses
            if d["scope"] in {s["scenario"] for s in played}
        ]

        chosen: list[tuple[str, str, str, dict[str, Any] | None]] = []
        seen_lower: set[str] = set()

        def add_candidate(name: str, objective: str) -> bool:
            low = name.lower()
            if low in seen_lower:
                return False
            stats = played_by_lower.get(low)
            cat_entry = CATALOG_BY_NAME_LOWER.get(low)
            if not stats and not cat_entry:
                return False
            src = "player_history" if stats else "verified_catalog"
            seen_lower.add(low)
            chosen.append((stats["scenario"] if stats else cat_entry.name, src, objective, stats))
            return True

        def prefer_history_for_categories(cats: tuple[str, ...], objective: str, limit: int = 1, max_tier: int = 3) -> None:
            added = 0
            # Prefer scenarios user already played in these categories
            matches = [s for s in played if any(c in s["categories"] for c in cats)]
            for s in matches:
                if len(chosen) >= target_count or added >= limit:
                    return
                if add_candidate(s["scenario"], objective):
                    added += 1
            # Fallback to verified catalog entries
            for entry in VERIFIED_SCENARIO_CATALOG:
                if len(chosen) >= target_count or added >= limit:
                    return
                if entry.difficulty_tier <= max_tier and any(c in entry.categories for c in cats):
                    if add_candidate(entry.name, objective):
                        added += 1

        if mode == "recovery":
            prefer_history_for_categories(("smooth_tracking",), "Low-tension smoothness & relaxed velocity matching", 1, max_tier=1)
            prefer_history_for_categories(("static_clicking",), "Calm micro-confirmation without rushing", 1, max_tier=1)
            prefer_history_for_categories(("precise_switching", "dynamic_clicking"), "Light pacing & visual target tracking", 1, max_tier=1)
        elif mode == "warmup_10":
            prefer_history_for_categories(("smooth_tracking",), "Warm up smooth mouse glide and eye tracking", 1, max_tier=1)
            prefer_history_for_categories(("static_clicking",), "Activate clean stopping & click confirmation", 1, max_tier=2)
            prefer_history_for_categories(("speed_switching", "dynamic_clicking"), "Wake up wide transitions and target reading", 1, max_tier=2)
        elif mode == "cs2_prep":
            for cat_entry_name, obj in (
                ("1wall6targets small", "CS2 micro-flick precision & crisp head-height stopping"),
                ("Pasu Voltaic Easy", "Counter-strafe & moving-target timing confirmation"),
                ("voxTargetSwitch Click", "Rapid multi-target spray/burst transfer discipline"),
                ("Smoothbot Voltaic Easy", "Smooth horizontal tracking & tension control"),
            ):
                if len(chosen) >= target_count:
                    break
                # Prefer user's played scenario in same primary category if they have one, else exact CS2 staple
                cat_obj = CATALOG_BY_NAME_LOWER[cat_entry_name.lower()]
                if cat_entry_name.lower() in played_by_lower:
                    add_candidate(cat_entry_name, obj)
                else:
                    matched_played = next((s for s in played if cat_obj.categories[0] in s["categories"]), None)
                    if matched_played and matched_played["scenario"].lower() not in seen_lower:
                        add_candidate(matched_played["scenario"], obj)
                    else:
                        add_candidate(cat_entry_name, obj)
        elif mode == "benchmark":
            # Include played benchmark scenarios first, then standard Voltaic novice/easy benchmarks
            benches = [s for s in played if "benchmark" in s["categories"]]
            for s in benches:
                if len(chosen) >= target_count:
                    break
                add_candidate(s["scenario"], "Measure current skill ceiling against personal benchmark baseline")
            for entry in VERIFIED_SCENARIO_CATALOG:
                if len(chosen) >= target_count:
                    break
                if "benchmark" in entry.categories:
                    add_candidate(entry.name, "Establish standardized benchmark baseline")
        elif mode == "focused_20":
            # Lead directly with diagnosed scenarios or weakest categories
            for s_name in diag_scenarios:
                if len(chosen) >= target_count:
                    break
                add_candidate(s_name, "Address primary detected performance bottleneck")
            for w_cat in weak_cats:
                if len(chosen) >= target_count:
                    break
                prefer_history_for_categories((w_cat,), f"Targeted development in lagging {SKILL_CATEGORY_MAP.get(w_cat, {}).get('label', w_cat)}", 1)
            prefer_history_for_categories(("smooth_tracking", "static_clicking", "dynamic_clicking"), "Reinforce complementary control", target_count)
        else:
            # balanced_30 or development_50
            prefer_history_for_categories(("smooth_tracking",), "Activation & tension control", 1, max_tier=2)
            for s_name in diag_scenarios[:2]:
                if len(chosen) >= target_count:
                    break
                add_candidate(s_name, "Primary bottleneck correction")
            for w_cat in weak_cats:
                if len(chosen) >= target_count:
                    break
                prefer_history_for_categories((w_cat,), f"Develop lagging {SKILL_CATEGORY_MAP.get(w_cat, {}).get('label', w_cat)}", 1)
            for fallback_cat, obj in (
                ("static_clicking", "Static precision & click confirmation"),
                ("dynamic_clicking", "Dynamic trajectory reading & timing"),
                ("speed_switching", "Target switching pace & crisp stops"),
                ("reactive_tracking", "Reactive direction-change tracking"),
                ("evasive_tracking", "Multi-axis tracking control"),
            ):
                if len(chosen) >= target_count:
                    break
                prefer_history_for_categories((fallback_cat,), obj, 1)

        # Guarantee we fill up to target_count if catalog still has items
        for entry in VERIFIED_SCENARIO_CATALOG:
            if len(chosen) >= target_count:
                break
            add_candidate(entry.name, f"Core {SKILL_CATEGORY_MAP.get(entry.categories[0], {}).get('label', 'aim')} training")

        return chosen[:target_count]

    def generate_plan(
        self,
        *,
        mode: str = "balanced_30",
        plan_date: str | None = None,
        readiness: dict[str, Any] | None = None,
        target_minutes: int | None = None,
        started_at: str | None = None,
    ) -> dict[str, Any]:
        """Generate and persist a personalized daily training plan."""
        clean_mode = (mode or "balanced_30").strip().lower()
        if clean_mode not in ROUTINE_MODE_MAP:
            raise ValueError(f"Unsupported routine mode '{mode}'. Choose from: {', '.join(ROUTINE_MODE_MAP)}.")

        target_day = date.fromisoformat(plan_date[:10]).isoformat() if plan_date else date.today().isoformat()
        latest_checkin = readiness if readiness is not None else self.memory.latest_readiness()
        readiness_state = self._assess_readiness(latest_checkin, clean_mode, target_minutes)

        effective_mode = readiness_state["effective_mode"]
        effective_minutes = readiness_state["effective_minutes"]
        mode_meta = ROUTINE_MODE_MAP[effective_mode]

        report = self.analytics.full_report(days=90)
        diagnoses = report.get("diagnoses", [])
        diag_ids = [d["id"] for d in diagnoses[:4]]

        if readiness_state["rest_advisory"]:
            plan_id = uuid.uuid4().hex
            start_ts = started_at or datetime.now().isoformat(sep=" ", timespec="seconds")
            readiness_blob = {**readiness_state, "started_at": start_ts}
            objective = "Rest & recovery advisory — prioritize hand/wrist recovery before resuming intense aim practice."
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO training_plans(
                        id, plan_date, mode, target_minutes, objective, readiness_json, diagnosis_ids_json, items_json, status
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'active')
                    """,
                    (
                        plan_id,
                        target_day,
                        effective_mode,
                        0,
                        objective,
                        json.dumps(readiness_blob, ensure_ascii=False),
                        json.dumps(diag_ids),
                        json.dumps([], ensure_ascii=False),
                    ),
                )
            return self.get_plan(plan_id)

        # Distribute minutes & reps across selected verified scenarios
        item_count = mode_meta["item_count"]
        if effective_minutes <= 12:
            item_count = min(item_count, 3)
        elif effective_minutes >= 45:
            item_count = max(item_count, 5)

        candidates = self._select_candidates(effective_mode, report, readiness_state, item_count)
        per_item_minutes = max(3, round(effective_minutes / max(1, len(candidates))))
        items: list[dict[str, Any]] = []
        for idx, (scen_name, source, role_obj, stats) in enumerate(candidates, start=1):
            cat_entry = CATALOG_BY_NAME_LOWER.get(scen_name.lower())
            tags = stats["categories"] if stats else self.taxonomy.get_tags(scen_name)["categories"]
            decision = compute_difficulty_decision(stats, cat_entry)

            # Suggest harder/easier variant explicitly when adaptive decision warrants it
            prescribed_scenario = scen_name
            variant_note = ""
            if decision["action"] == "progress_harder" and cat_entry and cat_entry.harder_variant and readiness_state["intensity"] == "normal":
                variant_note = f"Progression option: test {cat_entry.harder_variant} after hitting target here."
            elif decision["action"] == "reduce_prioritize_control" and cat_entry and cat_entry.easier_variant:
                variant_note = f"Control regression option: step down to {cat_entry.easier_variant} if CV stays high."

            target_runs = max(2, min(8, round(per_item_minutes / 1.5)))
            if effective_mode == "recovery" or readiness_state["intensity"] == "low_recovery":
                target_runs = min(target_runs, 3)
            elif effective_mode == "warmup_10":
                target_runs = 2

            cue = (
                cat_entry.technique_focus
                if cat_entry
                else "Focus on calm visual confirmation and steady pacing; treat cues as hypotheses to verify on feel or video."
            )
            if variant_note:
                cue = f"{cue} {variant_note}"

            perf_target = self._build_target_string(stats, decision)
            items.append({
                "order": idx,
                "scenario": prescribed_scenario,
                "source": source,
                "categories": tags,
                "duration_minutes": per_item_minutes,
                "target_runs": target_runs,
                "objective": role_obj,
                "technique_cue": cue,
                "performance_target": perf_target,
                "difficulty_rule": decision,
            })

        total_planned_mins = sum(item["duration_minutes"] for item in items)
        top_focus = diagnoses[0]["title"] if diagnoses else mode_meta["description"]
        objective = f"{mode_meta['label']} ({total_planned_mins} min) — Focus: {top_focus}"
        plan_id = uuid.uuid4().hex
        start_ts = started_at or datetime.now().isoformat(sep=" ", timespec="seconds")
        readiness_blob = {**readiness_state, "started_at": start_ts}

        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO training_plans(
                    id, plan_date, mode, target_minutes, objective, readiness_json, diagnosis_ids_json, items_json, status
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    plan_id,
                    target_day,
                    effective_mode,
                    total_planned_mins,
                    objective,
                    json.dumps(readiness_blob, ensure_ascii=False),
                    json.dumps(diag_ids),
                    json.dumps(items, ensure_ascii=False),
                ),
            )
        return self.get_plan(plan_id)

    def _hydrate_plan(self, row: Any) -> dict[str, Any]:
        plan = dict(row)
        plan["readiness"] = json.loads(plan.pop("readiness_json") or "{}")
        plan["diagnosis_ids"] = json.loads(plan.pop("diagnosis_ids_json") or "[]")
        plan["items"] = json.loads(plan.pop("items_json") or "[]")
        ev_raw = plan.pop("evaluation_json", None)
        plan["evaluation"] = json.loads(ev_raw) if ev_raw else None
        plan["mode_label"] = ROUTINE_MODE_MAP.get(plan["mode"], {}).get("label", plan["mode"])

        # Attach live progress against runs occurring >= started_at
        started_at = plan["readiness"].get("started_at") or plan["plan_date"]
        post_runs = self.database.normalized_runs(start=started_at)
        by_scen_lower: dict[str, list[dict[str, Any]]] = {}
        for r in post_runs:
            by_scen_lower.setdefault(r["scenario"].lower(), []).append(r)

        completed_items = 0
        for item in plan["items"]:
            matched = by_scen_lower.get(item["scenario"].lower(), [])
            scores = [r["score"] for r in matched if r["score"] is not None]
            med = _median(scores)
            best = max(scores) if scores else None
            acc = _weighted_accuracy(matched)
            t_score = item["performance_target"].get("target_score")
            t_acc = item["performance_target"].get("target_accuracy")
            target_runs = item["target_runs"]

            if not matched:
                t_status = "not_started"
            elif len(matched) < target_runs:
                t_status = "in_progress"
            else:
                score_ok = (t_score is None) or (med is not None and med >= t_score)
                acc_ok = (t_acc is None) or (acc is None) or (acc >= t_acc - 1.0)
                if score_ok and acc_ok:
                    t_status = "achieved"
                elif (t_score is not None and best is not None and best >= t_score) or score_ok or acc_ok:
                    t_status = "partially_achieved"
                else:
                    t_status = "missed"

            if len(matched) >= target_runs:
                completed_items += 1

            item["progress"] = {
                "completed_runs": len(matched),
                "target_runs": target_runs,
                "actual_median_score": round(med, 2) if med is not None else None,
                "actual_best_score": round(best, 2) if best is not None else None,
                "actual_accuracy": round(acc, 2) if acc is not None else None,
                "target_status": t_status,
            }

        total_items = len(plan["items"])
        plan["live_summary"] = {
            "completed_scenarios": completed_items,
            "total_scenarios": total_items,
            "completion_pct": round(completed_items / total_items * 100.0, 1) if total_items else 0.0,
            "runs_logged_since_plan": len(post_runs),
        }
        return plan

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM training_plans WHERE id = ?", (plan_id,)).fetchone()
        if not row:
            raise ValueError("Training plan not found.")
        return self._hydrate_plan(row)

    def get_active_plan(self, plan_date: str | None = None) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            if plan_date:
                row = connection.execute(
                    "SELECT * FROM training_plans WHERE plan_date = ? ORDER BY created_at DESC LIMIT 1",
                    (plan_date[:10],),
                ).fetchone()
            else:
                row = None
            if row is None:
                row = connection.execute(
                    "SELECT * FROM training_plans ORDER BY plan_date DESC, created_at DESC LIMIT 1"
                ).fetchone()
        return self._hydrate_plan(row) if row else None

    def list_plans(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM training_plans ORDER BY plan_date DESC, created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._hydrate_plan(row) for row in rows]

    def evaluate_plan(self, plan_id: str, subjective_feedback: str = "") -> dict[str, Any]:
        """Close the feedback loop: compare completed session performance to prescribed plan targets."""
        plan = self.get_plan(plan_id)
        feedback = str(subjective_feedback or "").strip()
        if len(feedback) > 1000:
            raise ValueError("Subjective feedback must be at most 1000 characters.")

        items = plan["items"]
        if not items:
            evaluation = {
                "completion_pct": 100.0 if plan["readiness"].get("rest_advisory") else 0.0,
                "targets_achieved": 0,
                "targets_total": 0,
                "pre_vs_post_delta_pct": None,
                "verdict": "rest_followed" if plan["readiness"].get("rest_advisory") else "incomplete",
                "next_session_change": "recheck_readiness",
                "summary": "Rest advisory recorded; check readiness before next active session.",
            }
        else:
            achieved = 0
            partial = 0
            completed = 0
            deltas: list[float] = []
            for item in items:
                prog = item["progress"]
                if prog["completed_runs"] > 0:
                    completed += 1
                if prog["target_status"] == "achieved":
                    achieved += 1
                elif prog["target_status"] == "partially_achieved":
                    partial += 1
                baseline = item["performance_target"].get("baseline_median")
                actual = prog.get("actual_median_score")
                if baseline and actual is not None and abs(baseline) > 1e-9:
                    deltas.append((actual - baseline) / abs(baseline) * 100.0)

            total = len(items)
            completion_pct = round(completed / total * 100.0, 1)
            avg_delta = round(sum(deltas) / len(deltas), 2) if deltas else None

            if completed == 0:
                verdict = "incomplete"
                next_change = "retain"
                summary = "No runs matching the prescribed scenarios were logged after this plan was created."
            elif achieved >= max(1, (total + 1) // 2) and (avg_delta is None or avg_delta >= 0.0):
                verdict = "effective"
                next_change = "progress" if achieved == total else "retain"
                summary = f"Targets achieved in {achieved}/{total} drills" + (f" ({avg_delta:+.2f}% median vs baseline)." if avg_delta is not None else ".")
            elif achieved + partial > 0 or (avg_delta is not None and avg_delta > -1.0):
                verdict = "mixed"
                next_change = "retain"
                summary = f"Partial gains ({achieved} met, {partial} partial across {completed}/{total} played drills)."
            else:
                verdict = "needs_adjustment"
                next_change = "reduce_for_control"
                summary = f"Targets missed across played drills ({avg_delta:+.2f}% vs baseline); step back difficulty or emphasize control next session." if avg_delta is not None else "Targets missed; adjust difficulty downward."

            evaluation = {
                "completion_pct": completion_pct,
                "targets_achieved": achieved,
                "targets_partial": partial,
                "targets_total": total,
                "pre_vs_post_delta_pct": avg_delta,
                "verdict": verdict,
                "next_session_change": next_change,
                "summary": summary,
            }

        now_ts = datetime.now().isoformat(sep=" ", timespec="seconds")
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE training_plans
                SET status = 'evaluated',
                    evaluation_json = ?,
                    subjective_feedback = ?,
                    outcome_verdict = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (json.dumps(evaluation, ensure_ascii=False), feedback, evaluation["verdict"], now_ts, plan_id),
            )
        return self.get_plan(plan_id)
