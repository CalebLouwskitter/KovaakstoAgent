from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.analytics import PerformanceAnalytics
from app.database import Database
from app.importer import import_file
from app.memory import CoachingMemory
from app.planner import MEDICAL_DISCLAIMER, ROUTINE_MODES, TrainingPlanner
from app.taxonomy import CATALOG_BY_NAME_LOWER, TaxonomyManager


class PlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "planner.db")
        self.memory = CoachingMemory(self.db)
        self.taxonomy = TaxonomyManager(self.db)
        self.analytics = PerformanceAnalytics(self.db, self.taxonomy)
        self.planner = TrainingPlanner(self.db, self.analytics, self.taxonomy, self.memory)

        sample = Path(__file__).resolve().parents[1] / "samples" / "demo-history.csv"
        import_file(self.db, sample)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_all_seven_routine_modes_produce_verified_complete_items(self) -> None:
        history_names = {r["scenario"].lower() for r in self.db.normalized_runs()}
        allowed_lower = set(CATALOG_BY_NAME_LOWER.keys()) | history_names

        for mode_meta in ROUTINE_MODES:
            mode = mode_meta["id"]
            with self.subTest(mode=mode):
                plan = self.planner.generate_plan(mode=mode, plan_date="2026-08-30")
                self.assertEqual(plan["mode"], mode)
                self.assertTrue(plan["items"])
                for item in plan["items"]:
                    self.assertIn(item["scenario"].lower(), allowed_lower)
                    self.assertGreater(item["duration_minutes"], 0)
                    self.assertGreater(item["target_runs"], 0)
                    self.assertTrue(item["objective"])
                    self.assertTrue(item["technique_cue"])
                    self.assertIn("summary", item["performance_target"])
                    self.assertIn("progress_if", item["difficulty_rule"])
                    self.assertIn("regress_if", item["difficulty_rule"])

    def test_fatigue_and_wrist_pain_guardrails(self) -> None:
        # High wrist discomfort (>= 4) triggers rest advisory and non-medical disclaimer
        pain_checkin = self.memory.save_readiness_checkin({
            "check_date": "2026-08-30",
            "energy": 3,
            "focus": 3,
            "fatigue": 3,
            "eye_strain": 2,
            "wrist_discomfort": 4,
            "available_minutes": 45,
        })
        rest_plan = self.planner.generate_plan(mode="development_50", plan_date="2026-08-30", readiness=pain_checkin)
        self.assertTrue(rest_plan["readiness"]["rest_advisory"])
        self.assertEqual(rest_plan["target_minutes"], 0)
        self.assertEqual(rest_plan["items"], [])
        self.assertEqual(rest_plan["readiness"]["medical_disclaimer"], MEDICAL_DISCLAIMER)

        # High fatigue (4) + moderate wrist (3) scales a heavy session down to recovery <= 15 min
        tired_checkin = self.memory.save_readiness_checkin({
            "check_date": "2026-08-31",
            "energy": 2,
            "focus": 2,
            "fatigue": 4,
            "eye_strain": 4,
            "wrist_discomfort": 3,
            "available_minutes": 60,
        })
        scaled_plan = self.planner.generate_plan(mode="development_50", plan_date="2026-08-31", readiness=tired_checkin)
        self.assertFalse(scaled_plan["readiness"]["rest_advisory"])
        self.assertEqual(scaled_plan["mode"], "recovery")
        self.assertLessEqual(scaled_plan["target_minutes"], 20)

    def test_closed_loop_plan_tracking_and_post_session_evaluation(self) -> None:
        # Create plan anchored at 2026-08-31 09:00:00
        plan = self.planner.generate_plan(
            mode="focused_20",
            plan_date="2026-08-31",
            started_at="2026-08-31 09:00:00",
        )
        self.assertGreaterEqual(len(plan["items"]), 2)
        first_scen = plan["items"][0]["scenario"]
        second_scen = plan["items"][1]["scenario"]
        target1 = (plan["items"][0]["performance_target"]["target_score"] or 500.0) + 25.0
        target2 = (plan["items"][1]["performance_target"]["target_score"] or 500.0) + 25.0

        # Simulate completing enough runs after plan started_at beating targets
        post_csv = self.root / "post_session.csv"
        rows = ["Timestamp,Scenario,Score,Accuracy,Duration,Shots,Hits,Misses"]
        for i in range(plan["items"][0]["target_runs"]):
            rows.append(f"2026-08-31 09:{10 + i:02d}:00,{first_scen},{target1},92.0%,60,150,138,12")
        for i in range(plan["items"][1]["target_runs"]):
            rows.append(f"2026-08-31 09:{25 + i:02d}:00,{second_scen},{target2},88.0%,60,150,132,18")
        post_csv.write_text("\n".join(rows) + "\n", encoding="utf-8")
        import_file(self.db, post_csv)

        active = self.planner.get_active_plan("2026-08-31")
        self.assertEqual(active["items"][0]["progress"]["target_status"], "achieved")
        self.assertGreaterEqual(active["live_summary"]["completed_scenarios"], 2)

        evaluated = self.planner.evaluate_plan(plan["id"], subjective_feedback="Felt locked in on confirmation.")
        self.assertEqual(evaluated["status"], "evaluated")
        self.assertEqual(evaluated["subjective_feedback"], "Felt locked in on confirmation.")
        self.assertIn(evaluated["evaluation"]["verdict"], {"effective", "mixed"})
        self.assertGreaterEqual(evaluated["evaluation"]["targets_achieved"], 2)


if __name__ == "__main__":
    unittest.main()
