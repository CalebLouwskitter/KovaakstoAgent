from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.importer import import_file
from app.model import LocalOpenAICompatibleClient, ModelError
from app.service import AppService


class CoachingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = AppService(self.root)
        sample = Path(__file__).resolve().parents[1] / "samples" / "demo-history.csv"
        import_file(self.service.database, sample)

    def tearDown(self) -> None:
        self.service.stop()
        self.temp.cleanup()

    def test_offline_deterministic_brief_and_qa_work_without_api_key(self) -> None:
        brief = self.service.generate_coaching_brief(period="monthly", anchor="2026-08-30", use_llm=False)
        self.assertEqual(brief["mode"], "local_deterministic")
        self.assertTrue(brief["main_finding"])
        self.assertTrue(brief["evidence"])
        self.assertIn(brief["confidence"]["level"], {"high", "medium", "low"})
        self.assertTrue(brief["recommended_action"])
        self.assertTrue(brief["recommended_scenarios"])
        self.assertTrue(brief["evaluation_criteria"])
        self.assertTrue(brief["evidence_inspector"]["supporting_runs"])

        qa = self.service.ask_coach("What am I improving at?", anchor="2026-08-30", use_llm=False)
        self.assertEqual(qa["mode"], "local_deterministic")
        self.assertIn("voxTargetSwitch Click", qa["answer"])

        qa_sens = self.service.ask_coach("How did my sensitivity change affect scores?", anchor="2026-08-30", use_llm=False)
        self.assertIn("Sensitivity", qa_sens["answer"])

    def test_validator_filters_imaginary_scenarios_and_flags_unproven_mechanics(self) -> None:
        mock_client = Mock()
        mock_client.respond.return_value = json.dumps({
            "main_finding": "You are overflicking on static targets with shaky tracking.",
            "confidence": {"level": "high", "rationale": "Consistent sample."},
            "technique_hypotheses": [{"cue": "Slow down micro-corrections", "requires_video_or_telemetry": True}],
            "recommended_action": "Train precision static clicking.",
            "training_duration": "20 minutes",
            "recommended_scenarios": ["1wall6targets small", "Imaginary Hallucinated Scenario 9000"],
            "expected_outcome": "Higher accuracy.",
            "evaluation_criteria": "Accuracy >= 88%.",
            "follow_up": "Next session",
        })

        brief = self.service.coaching.generate_brief(
            period="monthly",
            anchor="2026-08-30",
            use_llm=True,
            client_factory=lambda: mock_client,
            provider_name="openai",
            model_name="gpt-5.4-mini",
        )
        self.assertEqual(brief["mode"], "llm_enhanced")
        self.assertEqual(brief["recommended_scenarios"], ["1wall6targets small"])
        self.assertEqual(brief["validation"]["unverified_scenarios_filtered"], ["Imaginary Hallucinated Scenario 9000"])
        self.assertFalse(brief["validation"]["verified"])
        self.assertTrue(any("Epistemic check" in w for w in brief["validation"]["warnings"]))

    def test_malformed_json_and_provider_error_recovery(self) -> None:
        # Malformed LLM prose falls back cleanly without crashing
        bad_json_client = Mock()
        bad_json_client.respond.return_value = "Here is plain prose without JSON brackets."
        brief1 = self.service.coaching.generate_brief(
            period="monthly",
            anchor="2026-08-30",
            use_llm=True,
            client_factory=lambda: bad_json_client,
        )
        self.assertIn("llm_prose", brief1)
        self.assertTrue(brief1["recommended_scenarios"])  # preserved from deterministic planner

        # Provider ModelError triggers graceful deterministic fallback
        failing_client = Mock()
        failing_client.respond.side_effect = ModelError("OpenRouter request failed (429): Rate limit", {"http_status": 429})
        brief2 = self.service.coaching.generate_brief(
            period="monthly",
            anchor="2026-08-30",
            use_llm=True,
            client_factory=lambda: failing_client,
            fallback_on_error=True,
        )
        self.assertEqual(brief2["provider"], "local_deterministic")
        self.assertEqual(brief2["provider_error"]["diagnostics"]["http_status"], 429)

    def test_effectiveness_tracking_after_followup_runs(self) -> None:
        # Generate a coaching brief at 2026-08-30 and ensure subsequent improved runs are tracked
        self.service.generate_coaching_brief(period="monthly", anchor="2026-08-30", use_llm=False)
        followup = self.root / "followup.csv"
        followup.write_text(
            "Timestamp,Scenario,Score,Accuracy,Duration,Shots,Hits,Misses\n"
            "2026-08-31 19:00:00,1wall6targets small,990.0,91.0%,60,160,146,14\n"
            "2026-08-31 19:05:00,Smoothbot Voltaic Easy,3250.0,49.0%,60,1200,588,612\n",
            encoding="utf-8",
        )
        import_file(self.service.database, followup)

        outcomes = self.service.coaching.evaluate_recommendations()
        self.assertTrue(outcomes)
        rec = next(o for o in outcomes if o["source_type"] == "coaching_recommendation")
        self.assertEqual(rec["verdict"], "improved")
        self.assertGreater(rec["delta_pct"], 0.0)

    def test_local_loopback_provider_validation(self) -> None:
        remote = LocalOpenAICompatibleClient(endpoint="https://evil.example/v1/chat/completions")
        with self.assertRaisesRegex(ModelError, "127.0.0.1 or localhost"):
            remote.respond("sys", "prompt")


if __name__ == "__main__":
    unittest.main()
