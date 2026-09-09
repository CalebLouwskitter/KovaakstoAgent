from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.service import AppService


class ServiceTests(unittest.TestCase):
    def test_settings_import_and_status_flow(self):
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "", "OPENROUTER_API_KEY": "", "GEMINI_API_KEY": ""},
        ), tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            csv_path = root / "live.csv"
            day = datetime.now().strftime("%Y-%m-%d")
            csv_path.write_text(
                "Timestamp,Scenario,Score,Accuracy,Duration\n"
                f"{day} 10:00:00,Reflex Flick,415.2,91.5%,60\n",
                encoding="utf-8",
            )
            service = AppService(root)
            service.update_settings(str(csv_path), "gpt-5.4-mini")
            imported = service.import_now(force=True)
            status = service.status()
            self.assertEqual(imported[0]["inserted"], 1)
            self.assertEqual(status["summary"]["runs"], 1)
            self.assertEqual(status["model_provider_api_version"], 1)
            self.assertEqual(status["model"], "gpt-5.4-mini")
            self.assertFalse(status["api_key_connected"])

    def test_provider_models_and_session_keys_are_independent(self):
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "", "OPENROUTER_API_KEY": "", "GEMINI_API_KEY": ""},
        ), tempfile.TemporaryDirectory() as temp:
            service = AppService(temp)

            service.update_settings("", "google/gemini-3.7-flash", "openrouter")
            service.set_session_key("router-key", "openrouter")
            router_status = service.status()
            service.update_settings("", "gemini-3.7-flash", "gemini")
            gemini_status = service.status()

            self.assertEqual(router_status["provider"], "openrouter")
            self.assertEqual(router_status["model"], "google/gemini-3.7-flash")
            self.assertTrue(router_status["api_key_connected"])
            self.assertEqual(gemini_status["provider"], "gemini")
            self.assertFalse(gemini_status["api_key_connected"])
            providers = {item["id"]: item for item in gemini_status["providers"]}
            self.assertEqual(providers["openrouter"]["model"], "google/gemini-3.7-flash")
            self.assertTrue(providers["openrouter"]["api_key_connected"])
            self.assertEqual(providers["gemini"]["model"], "gemini-3.7-flash")

    def test_rejects_unknown_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AppService(temp)
            with self.assertRaisesRegex(ValueError, "Unsupported model provider"):
                service.update_settings("", "anything", "unknown")


if __name__ == "__main__":
    unittest.main()
