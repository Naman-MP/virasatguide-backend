"""Simple Gemini-first model calls, with Ollama as the optional fallback."""

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
    gemini_model = None
    if os.getenv("GEMINI_API_KEY", "").strip():
        gemini_model = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
    return {
        "gemini_model": gemini_model,
        "ollama_model": os.getenv("OLLAMA_MODEL", "").strip() or None,
        "ollama_think": os.getenv("OLLAMA_THINK", "").strip(),
    }


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
    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=5)) as client:
        result = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            json=body, headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"].strip()},
        )
    result.raise_for_status()
    try:
        data = result.json()
        if not isinstance(data, dict):
            raise ValueError("Expected an object")
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
        text_parts = []
        for part in candidate["content"]["parts"]:
            if not isinstance(part, dict) or part.get("thought"):
                continue
            text = part.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        return "".join(text_parts)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise GenerationError("Gemini returned incomplete or invalid output", 502) from exc


async def ollama(instruction, context, schema, max_tokens, temperature):
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")
    parsed = urlsplit(base)
    valid_address = parsed.scheme in {"http", "https"} and parsed.hostname
    has_extra_parts = parsed.username or parsed.password or parsed.query or parsed.fragment
    if not valid_address or has_extra_parts:
        raise GenerationError("Set OLLAMA_BASE_URL to your Ollama server URL")
    on_vercel = os.getenv("VERCEL") == "1" or os.getenv("VERCEL_ENV") == "production"
    local_address = parsed.hostname in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
    if on_vercel and local_address:
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
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5)) as client:
        result = await client.post(base + "/api/chat", json=body, headers=headers)
    result.raise_for_status()
    try:
        return result.json()["message"]["content"]
    except (KeyError, TypeError, ValueError) as exc:
        raise GenerationError("Ollama returned invalid output", 502) from exc


async def generate(instruction: str, context: str, *, schema: type[BaseModel] | None = None,
                   max_tokens=5000, temperature=0.2) -> str | BaseModel:
    """Return validated JSON data or story text, trying Gemini before Ollama."""
    config = generation_config()
    providers = []
    if config["gemini_model"]:
        providers.append(("Gemini", gemini))
    if config["ollama_model"]:
        providers.append(("Ollama", ollama))
    if not providers:
        raise GenerationError("Set GEMINI_API_KEY, or configure OLLAMA_MODEL and OLLAMA_BASE_URL")
    errors = []
    for provider_name, call_provider in providers:
        try:
            text = await call_provider(instruction, context, schema, max_tokens, temperature)
            if not isinstance(text, str) or not text.strip():
                raise GenerationError(f"{provider_name} returned empty output", 502)
            if schema:
                try:
                    return schema.model_validate_json(text)
                except (ValidationError, ValueError) as exc:
                    raise GenerationError(f"{provider_name} returned invalid structured output", 502) from exc
            return text.strip()
        except httpx.TimeoutException:
            error = GenerationError(f"{provider_name} timed out; please retry", 504)
        except httpx.HTTPError:
            error = GenerationError(f"{provider_name} request failed; check its key, model and server settings")
        except GenerationError as exc:
            if not exc.allow_fallback:
                raise
            error = exc
        logger.warning("AI provider %s failed (status %s)", provider_name, error.status_code)
        errors.append(str(error))
        last_error = error
    raise GenerationError("; ".join(errors), last_error.status_code) from last_error
