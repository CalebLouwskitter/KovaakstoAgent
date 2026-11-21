"""Adaptive multi-role coaching engine, output validator, and history Q&A.

Coordinates four analytical responsibilities over a single verified evidence packet:
1. Performance Analyst (deterministic stats & trend/bottleneck detection)
2. Progress Evaluator (compares past advice & training plans against subsequent runs)
3. Training Planner (generates readiness-aware drills from verified scenarios)
4. Technique Coach & Synthesizer (deterministic offline brief or validated LLM synthesis)
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Callable

from .analytics import EPISTEMIC_CAVEAT, PerformanceAnalytics, _median, _weighted_accuracy
from .memory import CoachingMemory
from .model import ModelError
from .planner import TrainingPlanner
from .taxonomy import CATALOG_BY_NAME_LOWER, SKILL_CATEGORY_MAP, TaxonomyManager

logger = logging.getLogger(__name__)

PHYSICAL_CAUSE_PATTERN = re.compile(
    r"\b(overflick(?:ing|ed)?|underflick(?:ing|ed)?|shaky tracking|mouse trajectory|"
    r"crosshair trajectory|excessive wrist tension|grip tension|micro-tremor)\b",
    re.IGNORECASE,
)


def _extract_json_block(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from raw text or markdown ```json code fences."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


class CoachingEngine:
    """Evidence-grounded coaching orchestrator, validator, and effectiveness tracker."""

    def __init__(
        self,
        database: Any,
        analytics: PerformanceAnalytics,
        planner: TrainingPlanner,
        taxonomy: TaxonomyManager,
        memory: CoachingMemory,
    ):
        self.database = database
        self.analytics = analytics
        self.planner = planner
        self.taxonomy = taxonomy
        self.memory = memory

    def _allowed_scenario_map(self, report: dict[str, Any]) -> dict[str, str]:
        allowed: dict[str, str] = {k: v.name for k, v in CATALOG_BY_NAME_LOWER.items()}
        for s in report.get("scenarios", []):
            allowed[s["scenario"].lower()] = s["scenario"]
        return allowed

    def evaluate_recommendations(self) -> list[dict[str, Any]]:
        """Compare saved coaching recommendations & training plans against subsequent observed runs."""
        all_runs = self.database.normalized_runs()
        evaluations: list[dict[str, Any]] = []

        # 1. Evaluated & active training plans
        for plan in self.planner.list_plans(limit=15):
            ev = plan.get("evaluation")
            evaluations.append({
                "source_type": "training_plan",
                "id": plan["id"],
                "created_at": plan["readiness"].get("started_at") or plan["created_at"],
                "focus": plan["objective"],
                "scenarios": [item["scenario"] for item in plan["items"]],
                "verdict": ev["verdict"] if ev else ("in_progress" if plan["live_summary"]["runs_logged_since_plan"] else "pending"),
                "summary": ev["summary"] if ev else f"{plan['live_summary']['completed_scenarios']}/{plan['live_summary']['total_scenarios']} drills completed since plan.",
                "delta_pct": ev.get("pre_vs_post_delta_pct") if ev else None,
                "subjective_feedback": plan.get("subjective_feedback") or "",
            })

        # 2. Stored coaching recommendations
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM coaching_recommendations ORDER BY created_at DESC LIMIT 15"
            ).fetchall()

        for row in rows:
            structured = json.loads(row["structured_json"])
            evidence = json.loads(row["evidence_json"])
            ref_ts = structured.get("reference_timestamp") or row["created_at"]
            rec_scenarios = structured.get("recommended_scenarios", [])
            baseline_map = {
                item["scenario"].lower(): item.get("median")
                for item in evidence.get("top_scenarios", [])
                if item.get("scenario")
            }

            post_runs = [r for r in all_runs if r["observed_at"] >= ref_ts]
            matched_post = (
                [r for r in post_runs if r["scenario"].lower() in {s.lower() for s in rec_scenarios}]
                if rec_scenarios
                else post_runs
            )
            deltas: list[float] = []
            by_s: dict[str, list[float]] = {}
            for r in matched_post:
                if r["score"] is not None:
                    by_s.setdefault(r["scenario"].lower(), []).append(r["score"])
            for s_low, scores in by_s.items():
                base = baseline_map.get(s_low)
                post_med = _median(scores)
                if base and post_med is not None and abs(base) > 1e-9:
                    deltas.append((post_med - base) / abs(base) * 100.0)

            avg_delta = round(sum(deltas) / len(deltas), 2) if deltas else None
            if not matched_post:
                verdict = "awaiting_runs"
                summary = "No follow-up runs recorded in recommended scenarios yet."
            elif avg_delta is not None and avg_delta >= 1.0:
                verdict = "improved"
                summary = f"Median improved {avg_delta:+.2f}% across {len(matched_post)} follow-up run(s)."
            elif avg_delta is not None and avg_delta <= -1.5:
                verdict = "declined"
                summary = f"Median dipped {avg_delta:+.2f}% across {len(matched_post)} follow-up run(s); revisit cue or difficulty."
            else:
                verdict = "stable"
                summary = f"{len(matched_post)} follow-up run(s) logged with flat/early variance."

            evaluations.append({
                "source_type": "coaching_recommendation",
                "id": row["id"],
                "created_at": row["created_at"],
                "focus": structured.get("main_finding", ""),
                "scenarios": rec_scenarios,
                "verdict": verdict,
                "summary": summary,
                "delta_pct": avg_delta,
                "subjective_feedback": "",
            })

        evaluations.sort(key=lambda item: item["created_at"], reverse=True)
        return evaluations[:20]

    def build_compressed_evidence(
        self,
        *,
        period: str = "weekly",
        anchor: str | None = None,
        role_id: str = "coach",
        days: int = 90,
    ) -> dict[str, Any]:
        """Construct a token-efficient, privacy-safe evidence packet shared across all coaching roles."""
        report = self.analytics.full_report(days=days, anchor=anchor)
        role = self.memory.role(role_id)
        notes = [
            {
                "id": n["id"],
                "kind": n["kind"],
                "scenario": n["scenario"],
                "body": n["body"][:400],
                "status": n["status"],
                "updated_at": n["updated_at"],
            }
            for n in self.memory.notes()
            if n["status"] != "archived"
        ][:16]

        top_scenarios = [
            {
                "scenario": s["scenario"],
                "categories": s["categories"],
                "runs": s["runs"],
                "active_days": s["active_days"],
                "median": s["score"]["median"],
                "best": s["score"]["best"],
                "cv_pct": s["score"]["cv_pct"],
                "consistency": s["consistency_score"],
                "weighted_accuracy": s["weighted_accuracy"],
                "velocity_pct_wk": s["velocity"]["pct_per_week"],
                "trend": s["velocity"]["trend_label"],
                "is_plateau": s["plateau"]["is_plateau"],
                "tradeoff": s["accuracy_speed_tradeoff"]["state"],
            }
            for s in report.get("scenarios", [])[:20]
        ]

        cat_profiles = [
            {
                "id": c["id"],
                "label": c["label"],
                "runs": c["runs"],
                "skill_index": c["skill_index"],
                "pb_proximity_pct": c["pb_proximity_pct"],
                "consistency_score": c["consistency_score"],
                "velocity_pct_per_week": c["velocity_pct_per_week"],
                "confidence": c["confidence"],
            }
            for c in report.get("category_summary", {}).get("categories", [])
            if c["runs"] > 0
        ]

        readiness = self.memory.latest_readiness()
        prior_outcomes = self.evaluate_recommendations()[:5]
        recent_runs = self.database.normalized_runs()[-15:]

        return {
            "schema_version": 2,
            "period": period,
            "anchor_date": report["anchor_date"],
            "reference_timestamp": report.get("latest_run") or datetime.now().isoformat(sep=" ", timespec="seconds"),
            "role": role,
            "lifetime_runs": report.get("lifetime_runs", 0),
            "active_days": report.get("active_days", 0),
            "rolling_windows": report.get("rolling_windows", {}),
            "category_profiles": cat_profiles,
            "strongest_categories": [c["label"] for c in report.get("category_summary", {}).get("strongest", [])],
            "weakest_categories": [c["label"] for c in report.get("category_summary", {}).get("weakest", [])],
            "top_scenarios": top_scenarios,
            "diagnoses": report.get("diagnoses", [])[:5],
            "session_progression": report.get("session_progression", {}),
            "hardware_changes": [
                {"observed_at": h["observed_at"], "summary": h["summary"], "impact": h.get("impact")}
                for h in report.get("hardware_events", [])[:4]
            ],
            "latest_readiness": readiness,
            "player_memory": notes,
            "prior_outcomes": prior_outcomes,
            "recent_runs_sample": [
                {
                    "observed_at": r["observed_at"],
                    "scenario": r["scenario"],
                    "score": r["score"],
                    "accuracy": r["accuracy"],
                    "duration_seconds": r["duration_seconds"],
                }
                for r in recent_runs
            ],
            "epistemic_caveat": EPISTEMIC_CAVEAT,
        }

    def _deterministic_brief(self, evidence: dict[str, Any], report: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """Synthesize a structured coaching brief strictly from deterministic analytics (offline mode)."""
        total_runs = evidence.get("lifetime_runs", 0)
        if total_runs == 0:
            return {
                "mode": "local_deterministic",
                "reference_timestamp": evidence["reference_timestamp"],
                "main_finding": "No training runs are currently imported.",
                "evidence": [{"label": "Dataset coverage", "metric": "0 runs retained", "sample_size": 0}],
                "confidence": {"level": "low", "rationale": "Import KovaaK's CSV stats or load the bundled sample history first."},
                "technique_hypotheses": [
                    {"cue": "Connect your KovaaK's stats folder or click Load Sample to analyze your baseline.", "requires_video_or_telemetry": False}
                ],
                "recommended_action": "Import existing stats or record 3–5 baseline scenarios.",
                "training_duration": "15 minutes",
                "recommended_scenarios": [item["scenario"] for item in plan.get("items", [])],
                "expected_outcome": "Establish reproducible baseline medians and accuracy metrics.",
                "evaluation_criteria": "Record >=3 runs per starter scenario with complete duration and shot stats.",
                "follow_up": "After your first session",
                "progress_evaluation": {"verdict": "no_prior_data", "summary": "No prior sessions on record."},
                "uncertainties_and_limits": [EPISTEMIC_CAVEAT],
                "validation": {"verified": True, "warnings": [], "unverified_scenarios_filtered": []},
                "evidence_inspector": {
                    "top_diagnoses": [],
                    "supporting_runs": [],
                    "rolling_windows": evidence.get("rolling_windows", {}),
                },
            }

        diagnoses = evidence.get("diagnoses", [])
        top_diag = diagnoses[0] if diagnoses else None
        weak_cats = evidence.get("weakest_categories", [])
        strong_cats = evidence.get("strongest_categories", [])
        prior_outcomes = evidence.get("prior_outcomes", [])

        if top_diag:
            main_finding = f"{top_diag['title']}: {top_diag['evidence_summary']}"
            conf_level = top_diag["confidence"]
            sample_sz = top_diag["sample_size"]
            conf_rationale = f"Observed across {sample_sz} run(s) / {top_diag['active_days']} active day(s)."
        elif weak_cats:
            main_finding = f"{weak_cats[0]} is your lowest normalized category relative to {strong_cats[0] if strong_cats else 'your baseline'}."
            conf_level = "medium" if total_runs >= 10 else "low"
            sample_sz = total_runs
            conf_rationale = f"Based on {total_runs} retained runs across {evidence.get('active_days', 0)} active day(s)."
        else:
            main_finding = f"Steady training baseline across {total_runs} retained runs."
            conf_level = "medium" if total_runs >= 10 else "low"
            sample_sz = total_runs
            conf_rationale = "No acute statistical anomalies detected in current sample."

        ev_items = []
        for d in diagnoses[:3]:
            ev_items.append({
                "label": d["title"],
                "metric": d["evidence_summary"],
                "sample_size": d["sample_size"],
                "scenario": d["scope"],
            })
        if not ev_items:
            for s in evidence.get("top_scenarios", [])[:3]:
                ev_items.append({
                    "label": s["scenario"],
                    "metric": f"Median {s['median']}, PB {s['best']}, CV {s['cv_pct']}%",
                    "sample_size": s["runs"],
                    "scenario": s["scenario"],
                })

        hypotheses = []
        for item in plan.get("items", [])[:3]:
            hypotheses.append({
                "cue": f"{item['scenario']}: {item['technique_cue']}",
                "requires_video_or_telemetry": True,
            })
        if top_diag:
            for alt in top_diag.get("alternative_explanations", [])[:2]:
                hypotheses.append({"cue": f"Alternative context to verify: {alt}", "requires_video_or_telemetry": True})

        rec_scenarios = [item["scenario"] for item in plan.get("items", [])]
        if plan["readiness"].get("rest_advisory"):
            rec_action = "Pause active mouse training and follow the rest advisory due to high self-reported wrist discomfort."
            duration_label = "0 minutes (rest day)"
            expected = "Prevent discomfort escalation and preserve recovery readiness."
            eval_criteria = "Re-run readiness check-in before resuming light training."
        else:
            first_drill = plan["items"][0] if plan["items"] else None
            rec_action = (
                (top_diag["recommended_focus"] if top_diag else f"Run {plan['mode_label']}")
                + (f" — start with {first_drill['scenario']} ({first_drill['performance_target']['summary']})." if first_drill else "")
            )
            duration_label = f"{plan['target_minutes']} minutes"
            expected = "Improve median consistency and hit prescribed target scores without degrading weighted accuracy."
            eval_criteria = (
                f"Achieve target score/accuracy in >= {max(1, len(rec_scenarios) // 2)}/{max(1, len(rec_scenarios))} planned drills "
                f"and log post-session evaluation."
            )

        if prior_outcomes:
            latest_out = prior_outcomes[0]
            prog_eval = {"verdict": latest_out["verdict"], "summary": f"{latest_out['focus']}: {latest_out['summary']}"}
        else:
            prog_eval = {"verdict": "baseline", "summary": "No prior evaluated plans or recommendations on record yet."}

        uncertainties = [EPISTEMIC_CAVEAT]
        if total_runs < 12:
            uncertainties.append(f"Small sample size ({total_runs} runs) limits statistical power; re-evaluate after 10–15 additional runs.")
        for d in diagnoses[:2]:
            if d["pattern_code"] == "inconsistent_run_duration":
                uncertainties.append("Mixed scenario durations detected; duration spread can distort raw score trends.")

        return {
            "mode": "local_deterministic",
            "reference_timestamp": evidence["reference_timestamp"],
            "main_finding": main_finding,
            "evidence": ev_items,
            "confidence": {"level": conf_level, "rationale": conf_rationale},
            "technique_hypotheses": hypotheses,
            "recommended_action": rec_action,
            "training_duration": duration_label,
            "recommended_scenarios": rec_scenarios,
            "expected_outcome": expected,
            "evaluation_criteria": eval_criteria,
            "follow_up": f"Next session or after 5–10 runs (check-in from {evidence['anchor_date']})",
            "progress_evaluation": prog_eval,
            "uncertainties_and_limits": uncertainties,
            "validation": {"verified": True, "warnings": [], "unverified_scenarios_filtered": []},
            "evidence_inspector": {
                "top_diagnoses": diagnoses[:3],
                "supporting_runs": evidence.get("recent_runs_sample", []),
                "rolling_windows": evidence.get("rolling_windows", {}),
            },
        }

    def validate_structured_coaching(
        self,
        raw_text: str,
        allowed_scenarios: dict[str, str],
        fallback_brief: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate LLM JSON response against verified scenarios and epistemic safety rules."""
        parsed = _extract_json_block(raw_text)
        warnings: list[str] = []
        filtered_scenarios: list[str] = []

        if parsed is None:
            result = dict(fallback_brief)
            result["mode"] = "llm_enhanced"
            result["llm_prose"] = raw_text.strip()[:4000]
            warnings.append("Model output was not strict JSON; retained deterministic structure alongside model summary.")
            result["validation"] = {
                "verified": False,
                "warnings": warnings,
                "unverified_scenarios_filtered": [],
            }
            return result

        merged = dict(fallback_brief)
        merged["mode"] = "llm_enhanced"

        for key in ("main_finding", "recommended_action", "training_duration", "expected_outcome", "evaluation_criteria", "follow_up"):
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                merged[key] = val.strip()[:1200]

        if isinstance(parsed.get("confidence"), dict):
            lvl = str(parsed["confidence"].get("level", "")).lower()
            if lvl in {"high", "medium", "low"}:
                merged["confidence"] = {
                    "level": lvl,
                    "rationale": str(parsed["confidence"].get("rationale") or fallback_brief["confidence"]["rationale"])[:500],
                }

        # Strict scenario whitelist verification against history + verified catalog
        raw_scens = parsed.get("recommended_scenarios")
        if isinstance(raw_scens, list):
            verified_scens: list[str] = []
            for item in raw_scens:
                clean = str(item or "").strip()
                if not clean:
                    continue
                canonical = allowed_scenarios.get(clean.lower())
                if canonical:
                    if canonical not in verified_scens:
                        verified_scens.append(canonical)
                else:
                    filtered_scenarios.append(clean)
            if filtered_scenarios:
                warnings.append(
                    f"Removed {len(filtered_scenarios)} unverified/imaginary scenario name(s): {', '.join(filtered_scenarios)}."
                )
            if verified_scens:
                merged["recommended_scenarios"] = verified_scens
            else:
                warnings.append("No valid verified scenarios remained from model output; reverted to deterministic plan scenarios.")

        # Epistemic check: guard against unproven mechanical assertions presented as facts
        prose_blob = " ".join([
            str(merged.get("main_finding", "")),
            str(merged.get("recommended_action", "")),
            str(merged.get("expected_outcome", "")),
        ])
        if PHYSICAL_CAUSE_PATTERN.search(prose_blob):
            warnings.append(
                "Epistemic check: CSV metrics cannot prove physical mouse-control errors (e.g., overflicking or shaky tracking) without video or telemetry."
            )
            if EPISTEMIC_CAVEAT not in merged.get("uncertainties_and_limits", []):
                merged["uncertainties_and_limits"] = [EPISTEMIC_CAVEAT] + list(merged.get("uncertainties_and_limits", []))

        if isinstance(parsed.get("technique_hypotheses"), list) and parsed["technique_hypotheses"]:
            hyps = []
            for h in parsed["technique_hypotheses"][:5]:
                if isinstance(h, str) and h.strip():
                    hyps.append({"cue": h.strip()[:400], "requires_video_or_telemetry": True})
                elif isinstance(h, dict) and str(h.get("cue", "")).strip():
                    hyps.append({"cue": str(h["cue"]).strip()[:400], "requires_video_or_telemetry": True})
            if hyps:
                merged["technique_hypotheses"] = hyps

        merged["validation"] = {
            "verified": len(filtered_scenarios) == 0,
            "warnings": warnings,
            "unverified_scenarios_filtered": filtered_scenarios,
        }
        return merged

    def generate_brief(
        self,
        *,
        period: str = "weekly",
        anchor: str | None = None,
        role_id: str = "coach",
        use_llm: bool = False,
        client_factory: Callable[[], Any] | None = None,
        provider_name: str = "local_deterministic",
        model_name: str = "deterministic-v0.3",
        fallback_on_error: bool = True,
        save: bool = True,
    ) -> dict[str, Any]:
        """Run coordinated multi-role coaching analysis (deterministic or validated LLM)."""
        evidence = self.build_compressed_evidence(period=period, anchor=anchor, role_id=role_id)
        report = self.analytics.full_report(days=90, anchor=anchor)
        plan = self.planner.get_active_plan(evidence["anchor_date"]) or self.planner.generate_plan(
            mode="balanced_30", plan_date=evidence["anchor_date"]
        )
        brief = self._deterministic_brief(evidence, report, plan)

        provider_error = None
        if use_llm and client_factory is not None:
            instructions = (
                "You are a competitive FPS aim coaching team (Performance Analyst, Technique Coach, Training Planner, Progress Evaluator). "
                "Respond strictly as a single JSON object with keys: main_finding, confidence ({level, rationale}), "
                "technique_hypotheses (list of {cue, requires_video_or_telemetry: true}), recommended_action, training_duration, "
                "recommended_scenarios (ONLY from scenarios in the evidence or well-known KovaaK's catalog), expected_outcome, "
                "evaluation_criteria, follow_up. Use ONLY the verified numbers provided. "
                "Never invent scores, share codes, or claim CSV stats prove overflicking, underflicking, shaky tracking, or mouse trajectory mechanics."
            )
            prompt = "Verified coaching evidence packet:\n" + json.dumps(evidence, ensure_ascii=False)
            try:
                raw_text = client_factory().respond(instructions, prompt)
                allowed = self._allowed_scenario_map(report)
                brief = self.validate_structured_coaching(raw_text, allowed, brief)
            except ModelError as exc:
                logger.warning("LLM coaching call failed; falling back to deterministic brief: %s", exc)
                provider_error = {"message": str(exc), "diagnostics": exc.diagnostics}
                if not fallback_on_error:
                    raise
                brief["validation"]["warnings"].append(f"Provider fallback active ({exc}); deterministic brief shown.")
                brief["provider_error"] = provider_error

        brief["provider"] = provider_name if (use_llm and provider_error is None) else "local_deterministic"
        brief["model"] = model_name if (use_llm and provider_error is None) else "deterministic-v0.3"
        brief["linked_plan_id"] = plan["id"]

        if save and evidence.get("lifetime_runs", 0) > 0:
            rec_id = uuid.uuid4().hex
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO coaching_recommendations(
                        id, period, anchor_date, role_id, structured_json, evidence_json, status
                    ) VALUES(?, ?, ?, ?, ?, ?, 'active')
                    """,
                    (
                        rec_id,
                        period,
                        evidence["anchor_date"],
                        role_id,
                        json.dumps(brief, ensure_ascii=False),
                        json.dumps(evidence, ensure_ascii=False),
                    ),
                )
            brief["id"] = rec_id
        return brief

    def latest_brief(self, period: str = "weekly", anchor: str | None = None) -> dict[str, Any]:
        """Return latest saved structured coaching brief, or synthesize an offline deterministic one on demand."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM coaching_recommendations ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row:
            brief = json.loads(row["structured_json"])
            brief["id"] = row["id"]
            brief["created_at"] = row["created_at"]
            return brief
        return self.generate_brief(period=period, anchor=anchor, use_llm=False, save=False)

    def ask(
        self,
        question: str,
        *,
        anchor: str | None = None,
        use_llm: bool = False,
        client_factory: Callable[[], Any] | None = None,
    ) -> dict[str, Any]:
        """Answer a natural-language question grounded strictly in the player's retained training data."""
        clean_q = (question or "").strip()
        if not clean_q or len(clean_q) > 600:
            raise ValueError("Ask a question between 1 and 600 characters.")

        report = self.analytics.full_report(days=0, anchor=anchor)
        if report["empty"]:
            return {
                "question": clean_q,
                "mode": "local_deterministic",
                "answer": "No training runs are imported yet. Connect your stats folder or click Load Sample History first.",
                "supporting_facts": [],
                "uncertainties": [EPISTEMIC_CAVEAT],
            }

        low_q = clean_q.lower()
        scenarios = report.get("scenarios", [])
        matched_scenarios = [s for s in scenarios if s["scenario"].lower() in low_q]
        if not matched_scenarios:
            tokens = [t for t in re.findall(r"[a-z0-9]+", low_q) if len(t) >= 3]
            for s in scenarios:
                s_low = s["scenario"].lower()
                if any(tok in s_low for tok in tokens):
                    matched_scenarios.append(s)

        matched_cats = [
            c for c in report["category_summary"]["categories"]
            if c["runs"] > 0 and (c["label"].lower() in low_q or c["short_label"].lower() in low_q)
        ]

        supporting_facts: list[str] = []
        for s in matched_scenarios[:4]:
            supporting_facts.append(
                f"{s['scenario']}: {s['runs']} runs, median {s['score']['median']}, PB {s['score']['best']}, "
                f"accuracy {s['weighted_accuracy']}%, velocity {s['velocity']['pct_per_week']}%/wk, CV {s['score']['cv_pct']}%."
            )
        for c in matched_cats[:3]:
            supporting_facts.append(
                f"{c['label']}: {c['runs']} runs, normalized skill index {c['skill_index']}/100, "
                f"PB proximity {c['pb_proximity_pct']}%, velocity {c['velocity_pct_per_week']}%/wk."
            )

        fq = report["five_questions"]
        if any(w in low_q for w in ("improv", "better", "progress", "fastest")):
            deterministic_answer = f"Improving: {fq['improving']}. {fq['measurable_progress']}"
        elif any(w in low_q for w in ("weak", "holding", "stuck", "plateau", "problem", "bad", "bottleneck")):
            deterministic_answer = f"Primary bottleneck: {fq['holding_back']}"
        elif any(w in low_q for w in ("sens", "dpi", "hardware", "gear", "mouse", "fov")):
            hws = report.get("hardware_events", [])
            if hws:
                ev = hws[0]
                imp = ev.get("impact") or {}
                if imp.get("comparable"):
                    deterministic_answer = (
                        f"Most recent setup change ({ev['observed_at'][:10]}): {ev['summary']}. "
                        f"Normalized performance changed {imp['index_delta_pct']:+.2f}% across {imp['after_runs']} post-change vs {imp['before_runs']} prior runs."
                    )
                else:
                    deterministic_answer = f"Latest setup change ({ev['observed_at'][:10]}): {ev['summary']} (insufficient paired runs before/after yet)."
            else:
                deterministic_answer = "No sensitivity, DPI, FOV, or manual hardware changes were detected in retained runs."
        elif any(w in low_q for w in ("today", "train", "plan", "routine", "next")):
            deterministic_answer = f"Recommended focus: {fq['train_today']}. Next change: {fq['next_session_change']}"
        elif matched_scenarios:
            deterministic_answer = " ".join(supporting_facts)
        else:
            deterministic_answer = (
                f"Summary across {report['lifetime_runs']} runs ({report['active_days']} active days): "
                f"Improving: {fq['improving']}. Primary focus: {fq['holding_back']}"
            )

        if not supporting_facts:
            supporting_facts = [
                f"Lifetime: {report['lifetime_runs']} runs across {report['active_days']} active days.",
                f"30d window: {report['rolling_windows']['30d']['runs']} runs.",
            ]

        answer = deterministic_answer
        mode = "local_deterministic"
        warnings: list[str] = []

        if use_llm and client_factory is not None:
            context = {
                "question": clean_q,
                "five_questions": fq,
                "matched_scenarios": matched_scenarios[:4],
                "matched_categories": matched_cats[:3],
                "top_diagnoses": report.get("diagnoses", [])[:3],
                "hardware_events": report.get("hardware_events", [])[:3],
                "rolling_windows": report.get("rolling_windows", {}),
            }
            try:
                llm_reply = client_factory().respond(
                    "Answer the player's question concisely using ONLY the supplied verified statistics. "
                    "Never invent numbers or scenario names, and explicitly state uncertainty if sample sizes are small.",
                    json.dumps(context, ensure_ascii=False),
                )
                if llm_reply.strip():
                    answer = llm_reply.strip()
                    mode = "llm_enhanced"
            except ModelError as exc:
                warnings.append(f"Provider fallback active ({exc}); deterministic answer shown.")

        return {
            "question": clean_q,
            "mode": mode,
            "answer": answer,
            "supporting_facts": supporting_facts,
            "warnings": warnings,
            "uncertainties": [EPISTEMIC_CAVEAT],
        }
