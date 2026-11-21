"""Simple Gemini-first model calls, with Ollama as the optional fallback."""

import json
import logging
import os
import re
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
logger = logging.getLogger(__name__)


class GenerationError(Exception):
    def __init__(self, message: str, status_code=503, *, allow_fallback=True):
        super().__init__(message)
        self.status_code = status_code
        self.allow_fallback = allow_fallback


def generation_config() -> dict:
    """Cache identity and storage metadata; never includes credentials."""
    return {
        "gemini_model": (os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL)
        if os.getenv("GEMINI_API_KEY", "").strip() else None,
        "ollama_model": os.getenv("OLLAMA_MODEL", "").strip() or None,
        "ollama_think": os.getenv("OLLAMA_THINK", "").strip(),
    }


async def post_json(url, body, headers, provider, timeout):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5)) as client:
            result = await client.post(url, json=body, headers=headers)
    except httpx.TimeoutException as exc:
        raise GenerationError(f"{provider} timed out; please retry", 504) from exc
    except httpx.HTTPError as exc:
        raise GenerationError(f"{provider} is unavailable; check its server configuration") from exc
    if result.status_code != 200:
        # Do not return upstream response bodies: they can contain private data.
        raise GenerationError(f"{provider} rejected the request (HTTP {result.status_code}); check its key, model and quota")
    try:
        data = result.json()
        if not isinstance(data, dict):
            raise ValueError("Expected an object")
        return data
    except ValueError as exc:
        raise GenerationError(f"{provider} returned invalid output", 502) from exc


async def gemini(instruction, context, schema, max_tokens, temperature):
    model = generation_config()["gemini_model"]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", model):
        raise GenerationError("Set GEMINI_MODEL to a valid Gemini model ID")
    config = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if schema:
        config.update(responseMimeType="application/json", responseJsonSchema=schema.model_json_schema())
    body = {
        "systemInstruction": {"parts": [{"text": instruction}]},
        "contents": [{"role": "user", "parts": [{"text": context}]}],
        "generationConfig": config,
    }
    data = await post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        body, {"x-goog-api-key": os.environ["GEMINI_API_KEY"].strip()}, "Gemini", 45,
    )
    try:
        feedback = data.get("promptFeedback") or {}
        if not isinstance(feedback, dict):
            raise ValueError("Invalid feedback")
        if feedback.get("blockReason"):
            raise GenerationError("Gemini declined this request", 502, allow_fallback=False)
        candidate = data["candidates"][0]
        if not isinstance(candidate, dict):
            raise ValueError("Invalid candidate")
        reason = candidate.get("finishReason")
        if reason in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
            raise GenerationError("Gemini declined this request", 502, allow_fallback=False)
        if reason != "STOP":
            raise ValueError("Incomplete response")
        return "".join(part["text"] for part in candidate["content"]["parts"]
                       if isinstance(part, dict) and not part.get("thought") and isinstance(part.get("text"), str))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise GenerationError("Gemini returned incomplete or invalid output", 502) from exc


async def ollama(instruction, context, schema, max_tokens, temperature):
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise GenerationError("Set OLLAMA_BASE_URL to your Ollama server URL")
    if (os.getenv("VERCEL") == "1" or os.getenv("VERCEL_ENV") == "production") and parsed.hostname in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        raise GenerationError("Vercel needs a hosted OLLAMA_BASE_URL; localhost cannot reach your Mac")
    body = {
        "model": generation_config()["ollama_model"], "stream": False,
        "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": context}],
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    if schema:
        body["format"] = schema.model_json_schema()
    if os.getenv("OLLAMA_THINK") == "false":
        body["think"] = False
    headers = {}
    if os.getenv("OLLAMA_API_KEY", "").strip():
        headers["Authorization"] = "Bearer " + os.environ["OLLAMA_API_KEY"].strip()
    data = await post_json(base + "/api/chat", body, headers, "Ollama", 180)
    try:
        return data["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise GenerationError("Ollama returned invalid output", 502) from exc


async def generate(instruction: str, context: str, *, schema: type[BaseModel] | None = None,
                   max_tokens=5000, temperature=0.2) -> str:
    """Validate each provider's result before accepting it. At most two calls."""
    config = generation_config()
    providers = ([gemini] if config["gemini_model"] else []) + ([ollama] if config["ollama_model"] else [])
    if not providers:
        raise GenerationError("Set GEMINI_API_KEY, or configure OLLAMA_MODEL and OLLAMA_BASE_URL")
    errors = []
    for provider in providers:
        try:
            text = await provider(instruction, context, schema, max_tokens, temperature)
            if not isinstance(text, str) or not text.strip():
                raise GenerationError(f"{provider.__name__.title()} returned empty output", 502)
            if schema:
                try:
                    schema.model_validate_json(text)
                except (ValidationError, ValueError) as exc:
                    raise GenerationError(f"{provider.__name__.title()} returned invalid structured output", 502) from exc
            return text.strip()
        except GenerationError as exc:
            if not exc.allow_fallback:
                raise
            logger.warning("AI provider %s failed (status %s)", provider.__name__, exc.status_code)
            errors.append(str(exc))
            last_error = exc
    raise GenerationError("; ".join(errors), last_error.status_code) from last_error
