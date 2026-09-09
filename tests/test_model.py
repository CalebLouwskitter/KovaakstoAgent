import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from app.model import (
    GeminiInteractionsClient,
    ModelError,
    OpenRouterChatClient,
    extract_chat_text,
    extract_gemini_text,
    extract_output_text,
)


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.body


class ModelTests(unittest.TestCase):
    def test_extracts_responses_api_text(self):
        payload = {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "Connection successful"}]}
            ]
        }
        self.assertEqual(extract_output_text(payload), "Connection successful")

    def test_extracts_openrouter_chat_text(self):
        payload = {"choices": [{"message": {"content": "Connection successful"}}]}
        self.assertEqual(extract_chat_text(payload), "Connection successful")

    def test_extracts_gemini_interaction_text(self):
        payload = {
            "steps": [
                {
                    "type": "model_output",
                    "content": [{"type": "text", "text": "Connection successful"}],
                }
            ]
        }
        self.assertEqual(extract_gemini_text(payload), "Connection successful")

    @patch("app.model.urllib.request.urlopen")
    def test_openrouter_uses_chat_completions_contract(self, urlopen):
        urlopen.return_value = FakeResponse(
            {"choices": [{"message": {"role": "assistant", "content": "Connection successful"}}]}
        )
        client = OpenRouterChatClient(api_key="router-key", model="anthropic/claude-sonnet")

        result = client.respond("System rules", "Player evidence")

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(result, "Connection successful")
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(headers["authorization"], "Bearer router-key")
        self.assertEqual(headers["x-openrouter-title"], "Kovaak Agent")
        self.assertEqual(headers["x-openrouter-metadata"], "enabled")
        self.assertEqual(body["model"], "anthropic/claude-sonnet")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "System rules"})
        self.assertEqual(body["messages"][1], {"role": "user", "content": "Player evidence"})

    @patch("app.model.urllib.request.urlopen")
    def test_openrouter_429_exposes_safe_diagnostics_and_redacts_secrets(self, urlopen):
        payload = {
            "error": {
                "message": "Provider returned error for sk-or-v1-secretvalue",
                "code": 429,
                "type": "provider_error",
                "metadata": {
                    "provider_name": "ExampleFreeProvider",
                    "raw": '{"error":"rate limit exceeded"}',
                    "api_key": "short-secret",
                },
            },
            "openrouter_metadata": {
                "attempt": 1,
                "attempts": [{"provider": "ExampleFreeProvider", "status": 429}],
            },
        }
        headers = {
            "Retry-After": "30",
            "X-Request-Id": "req_123",
            "X-Generation-Id": "gen_123",
        }
        urlopen.side_effect = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            429,
            "Too Many Requests",
            headers,
            io.BytesIO(json.dumps(payload).encode("utf-8")),
        )
        client = OpenRouterChatClient(api_key="router-key", model="z-ai/glm-5.2:free")

        with self.assertRaises(ModelError) as raised:
            client.respond("System rules", "Player evidence")

        error = raised.exception
        self.assertEqual(
            str(error),
            "OpenRouter request failed (429): Provider returned error for [redacted-api-key]",
        )
        self.assertEqual(error.diagnostics["provider"], "OpenRouter")
        self.assertEqual(error.diagnostics["http_status"], 429)
        self.assertEqual(error.diagnostics["error_code"], 429)
        self.assertEqual(error.diagnostics["error_type"], "provider_error")
        self.assertEqual(error.diagnostics["error_metadata"]["api_key"], "[redacted]")
        self.assertEqual(
            error.diagnostics["openrouter_metadata"]["attempts"][0]["provider"],
            "ExampleFreeProvider",
        )
        self.assertEqual(error.diagnostics["response_headers"]["retry-after"], "30")
        self.assertEqual(error.diagnostics["response_headers"]["x-request-id"], "req_123")
        self.assertEqual(error.diagnostics["response_headers"]["x-generation-id"], "gen_123")

    @patch("app.model.urllib.request.urlopen")
    def test_gemini_uses_native_interactions_contract(self, urlopen):
        urlopen.return_value = FakeResponse(
            {
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": "Connection successful"}],
                    }
                ],
            }
        )
        client = GeminiInteractionsClient(api_key="gemini-key", model="gemini-3.7-flash")

        result = client.respond("System rules", "Player evidence")

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(result, "Connection successful")
        self.assertEqual(request.full_url, "https://generativelanguage.googleapis.com/v1beta/interactions")
        self.assertEqual(headers["x-goog-api-key"], "gemini-key")
        self.assertEqual(body["model"], "gemini-3.7-flash")
        self.assertEqual(body["system_instruction"], "System rules")
        self.assertEqual(body["input"], "Player evidence")
        self.assertFalse(body["store"])


if __name__ == "__main__":
    unittest.main()
