"""LLM provider integrations and client implementations for Kovaak Agent.

Integrates with three providers using raw HTTP requests (zero external SDKs):
1. OpenAI: Uses the Responses API (POST /v1/responses) with store: False.
2. OpenRouter: Uses OpenAI-compatible Chat Completions (POST /api/v1/chat/completions).
3. Google Gemini: Uses the native Interactions API (POST /v1beta/interactions) with store: False.

Security & Diagnostics:
- Intercepts all upstream errors and redacts API keys/Bearer tokens via `_redact_secrets`.
- Surfaces safe debugging diagnostics (status codes, retry headers, model IDs) to the UI.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


class ModelError(RuntimeError):
    """Raised when an LLM provider request fails, carrying secret-redacted diagnostics."""

    def __init__(self, message: str, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class ModelClient(Protocol):
    """Common interface implemented by all LLM provider clients."""

    def respond(self, instructions: str, prompt: str) -> str: ...


def extract_output_text(payload: dict[str, Any]) -> str:
    """Extract model output text from OpenAI Responses API format."""
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()
    chunks: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    return "\n".join(chunks).strip()


def extract_chat_text(payload: dict[str, Any]) -> str:
    """Extract assistant message content from standard Chat Completions JSON format (OpenRouter)."""
    choices = payload.get("choices", [])
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message", {})
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    chunks: list[str] = []
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if part.get("type") in {"text", "output_text"} and isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


def extract_gemini_text(payload: dict[str, Any]) -> str:
    """Extract model output text from Google Gemini Interactions API format."""
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()
    chunks: list[str] = []
    for step in payload.get("steps", []):
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for content in step.get("content", []):
            if not isinstance(content, dict) or content.get("type") != "text":
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


# Regex patterns to scrub API keys, Bearer tokens, and secrets from diagnostics and logs
SECRET_PATTERNS = (
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~-]+"), r"\1[redacted]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "[redacted-api-key]"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{8,}\b"), "[redacted-api-key]"),
)

SECRET_FIELD_NAMES = {
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "x_api_key",
}


def _redact_secrets(value: Any) -> Any:
    """Recursively scrub known secret headers and API key regex patterns from data structures."""
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key)
            normalized_key = key_text.lower().replace("-", "_")
            redacted[key_text] = (
                "[redacted]"
                if normalized_key in SECRET_FIELD_NAMES
                else _redact_secrets(item)
            )
        return redacted
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if not isinstance(value, str):
        return value
    redacted = value
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _parse_error_detail(detail: str) -> tuple[str, dict[str, Any]]:
    """Parse JSON or raw string error response, returning a clean message and sanitized diagnostics."""
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        return detail, {"raw_response": _redact_secrets(detail[:4000])}
    diagnostics: dict[str, Any] = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        for key in ("code", "type", "metadata"):
            if key in error:
                diagnostics[f"error_{key}"] = _redact_secrets(error[key])
        if isinstance(error.get("message"), str):
            message = error["message"]
        else:
            message = detail
    if isinstance(error, str):
        message = error
    elif not isinstance(error, dict):
        message = detail
    if isinstance(payload, dict) and "openrouter_metadata" in payload:
        diagnostics["openrouter_metadata"] = _redact_secrets(payload["openrouter_metadata"])
    return str(_redact_secrets(message)), diagnostics


def _http_error_diagnostics(
    exc: urllib.error.HTTPError,
    detail: str,
    provider_name: str,
    endpoint: str,
) -> tuple[str, dict[str, Any]]:
    """Extract and scrub HTTP error metadata, status code, and relevant upstream response headers."""
    message, diagnostics = _parse_error_detail(detail)
    diagnostics = {
        "provider": provider_name,
        "http_status": exc.code,
        "endpoint": endpoint,
        **diagnostics,
    }
    response_headers = {}
    for name in ("Retry-After", "X-Request-Id", "X-Generation-Id", "CF-Ray"):
        value = exc.headers.get(name) if exc.headers else None
        if value:
            response_headers[name.lower()] = _redact_secrets(value)
    if response_headers:
        diagnostics["response_headers"] = response_headers
    return message, diagnostics


def _post_json(
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
    provider_name: str,
) -> dict[str, Any]:
    """Execute a synchronous POST request with JSON payload using standard library urllib."""
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        message, diagnostics = _http_error_diagnostics(exc, detail, provider_name, endpoint)
        raise ModelError(
            f"{provider_name} request failed ({exc.code}): {message}",
            diagnostics,
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ModelError(
            f"Could not reach the {provider_name} API: {exc}",
            {"provider": provider_name, "endpoint": endpoint, "error_type": type(exc).__name__},
        ) from exc
    except json.JSONDecodeError as exc:
        raise ModelError(f"{provider_name} returned an invalid JSON response.") from exc
    if not isinstance(result, dict):
        raise ModelError(f"{provider_name} returned an unexpected response.")
    return result


def _require_key(api_key: str, provider_name: str, environment_name: str) -> None:
    """Validate that an API key is present before dispatching an upstream network call."""
    if not api_key.strip():
        raise ModelError(
            f"No {provider_name} API key is connected. Add one for this session or set {environment_name}."
        )


@dataclass
class OpenAIResponsesClient:
    """Client for the OpenAI Responses API (POST /v1/responses) with store: False."""
    api_key: str
    model: str = "gpt-5.4-mini"
    endpoint: str = "https://api.openai.com/v1/responses"
    timeout_seconds: int = 45

    def respond(self, instructions: str, prompt: str) -> str:
        _require_key(self.api_key, "OpenAI", "OPENAI_API_KEY")
        payload = _post_json(
            self.endpoint,
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            {
                "model": self.model,
                "instructions": instructions,
                "input": prompt,
                "store": False,
                "text": {"verbosity": "low"},
            },
            self.timeout_seconds,
            "OpenAI",
        )
        output = extract_output_text(payload)
        if not output:
            raise ModelError("OpenAI returned no text output.")
        return output


@dataclass
class OpenRouterChatClient:
    """Client for OpenRouter's OpenAI-compatible Chat Completions API."""
    api_key: str
    model: str = "~openai/gpt-latest"
    endpoint: str = "https://openrouter.ai/api/v1/chat/completions"
    timeout_seconds: int = 45
    app_url: str = "http://127.0.0.1:8765"
    app_name: str = "Kovaak Agent"

    def respond(self, instructions: str, prompt: str) -> str:
        _require_key(self.api_key, "OpenRouter", "OPENROUTER_API_KEY")
        payload = _post_json(
            self.endpoint,
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": self.app_url,
                "X-OpenRouter-Title": self.app_name,
                "X-OpenRouter-Metadata": "enabled",
            },
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": prompt},
                ],
            },
            self.timeout_seconds,
            "OpenRouter",
        )
        output = extract_chat_text(payload)
        if not output:
            raise ModelError("OpenRouter returned no text output.")
        return output


@dataclass
class GeminiInteractionsClient:
    """Client for Google Gemini's native Interactions API with store: False."""
    api_key: str
    model: str = "gemini-3.7-flash"
    endpoint: str = "https://generativelanguage.googleapis.com/v1beta/interactions"
    timeout_seconds: int = 45

    def respond(self, instructions: str, prompt: str) -> str:
        _require_key(self.api_key, "Gemini", "GEMINI_API_KEY")
        payload = _post_json(
            self.endpoint,
            {
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            {
                "model": self.model,
                "system_instruction": instructions,
                "input": prompt,
                "store": False,
            },
            self.timeout_seconds,
            "Gemini",
        )
        output = extract_gemini_text(payload)
        if not output:
            raise ModelError("Gemini returned no text output.")
        return output


@dataclass(frozen=True)
class ProviderDefinition:
    """Metadata and factory registration for a supported LLM provider."""
    id: str
    label: str
    default_model: str
    api_key_environment: str
    model_environment: str
    client_type: type = field(repr=False)

    def create_client(self, api_key: str, model: str) -> ModelClient:
        return self.client_type(api_key=api_key, model=model)


# Registry of supported providers with environment variable fallbacks and client classes
PROVIDERS = {
    "openai": ProviderDefinition(
        "openai", "OpenAI", "gpt-5.4-mini", "OPENAI_API_KEY", "OPENAI_MODEL", OpenAIResponsesClient
    ),
    "openrouter": ProviderDefinition(
        "openrouter",
        "OpenRouter",
        "~openai/gpt-latest",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        OpenRouterChatClient,
    ),
    "gemini": ProviderDefinition(
        "gemini", "Gemini", "gemini-3.7-flash", "GEMINI_API_KEY", "GEMINI_MODEL", GeminiInteractionsClient
    ),
}


def get_provider(provider: str) -> ProviderDefinition:
    """Look up a provider definition by ID (case-insensitive) or raise ValueError with supported options."""
    provider_id = provider.strip().lower()
    try:
        return PROVIDERS[provider_id]
    except KeyError as exc:
        supported = ", ".join(item.label for item in PROVIDERS.values())
        raise ValueError(f"Unsupported model provider '{provider}'. Choose {supported}.") from exc


def create_model_client(provider: str, api_key: str, model: str) -> ModelClient:
    """Instantiate a configured ModelClient for the specified provider ID."""
    return get_provider(provider).create_client(api_key, model)
