"""Provider contract checks; HTTP requests are simulated, never billed."""

import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException

from virasatguide_ai_agent import generate_story, providers
import virasatguide_ai_agent.graph as story_graph
from virasatguide_ai_agent.graph import AgentError
from virasatguide_backend.data.stories import story_key_for
from virasatguide_backend.travel.llm import generate_json
from virasatguide_backend.travel.schemas import AnswerDraft
from test_ai_agent import MONUMENT

ANSWER = {"answer": "Shah Jahan commissioned it.", "supported": True, "evidence_ids": ["reference-1"]}


def gemini_response(text, **changes):
    """Build a response shaped like Gemini's generateContent result."""
    candidate = {"finishReason": "STOP", "content": {"parts": [{"text": text}]}}
    candidate.update(changes)
    return httpx.Response(200, json={"candidates": [candidate]})


@pytest.fixture(autouse=True)
def no_live_search(monkeypatch):
    """Disable public web search in provider tests."""
    monkeypatch.setattr(story_graph, "search_monument", lambda *_: [])


@pytest.fixture
def transport(monkeypatch):
    """Provide a test HTTP transport without calling a real provider."""
    original = httpx.AsyncClient
    def install(handler):
        """Connect a simulated HTTP transport to the specified provider handler."""
        monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    return install


def test_gemini_is_primary_with_validated_json(monkeypatch, transport):
    """Verify Gemini is primary with validated JSON."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-private-key")
    seen = []
    def handle(request):
        """Act as the upstream transport for the response or error under test."""
        seen.append(request.url.host)
        assert request.headers["x-goog-api-key"] == "test-private-key"
        assert "test-private-key" not in str(request.url)
        body = json.loads(request.content)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["responseJsonSchema"] == AnswerDraft.model_json_schema()
        assert "untrusted" in body["systemInstruction"]["parts"][0]["text"]
        assert json.loads(body["contents"][0]["parts"][0]["text"]) == {"question": "Who?"}
        return gemini_response(json.dumps(ANSWER))
    transport(handle)
    result = asyncio.run(generate_json(AnswerDraft, "Answer", {"question": "Who?"}))
    assert result.answer == ANSWER["answer"]
    assert seen == ["generativelanguage.googleapis.com"]


@pytest.mark.parametrize("failure", ["timeout", "offline", "quota", "auth", "unavailable", "bad_json", "wrong_schema", "empty", "truncated", "bad_candidate"])
def test_gemini_failure_uses_ollama_once(monkeypatch, transport, failure):
    """Verify Gemini failure uses Ollama once."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    seen = []
    def handle(request):
        """Act as the upstream transport for the response or error under test."""
        seen.append(request.url.host)
        if request.url.host == "generativelanguage.googleapis.com":
            if failure == "timeout": raise httpx.ReadTimeout("slow")
            if failure == "offline": raise httpx.ConnectError("offline")
            if failure in {"quota", "auth", "unavailable"}:
                return httpx.Response({"quota": 429, "auth": 403, "unavailable": 503}[failure], json={"error": "private upstream message"})
            if failure == "bad_json": return httpx.Response(200, text="not JSON")
            if failure == "wrong_schema": return gemini_response('{"answer": 9}')
            if failure == "empty": return gemini_response("  ")
            if failure == "truncated": return gemini_response("{}", finishReason="MAX_TOKENS")
            return httpx.Response(200, json={"candidates": [None]})
        body = json.loads(request.content)
        assert body["format"] == AnswerDraft.model_json_schema()
        assert body["think"] is False
        return httpx.Response(200, json={"message": {"content": json.dumps(ANSWER)}})
    transport(handle)
    assert asyncio.run(generate_json(AnswerDraft, "Answer", {})).supported
    assert seen == ["generativelanguage.googleapis.com", "127.0.0.1"]


def test_missing_gemini_key_uses_ollama_directly(transport):
    """Verify missing Gemini key uses Ollama directly."""
    hosts = []
    def handle(request):
        """Act as the upstream transport for the response or error under test."""
        hosts.append(request.url.host)
        return httpx.Response(200, json={"message": {"content": "Local story"}})
    transport(handle)
    assert asyncio.run(providers.generate("Write", "Facts")) == "Local story"
    assert hosts == ["127.0.0.1"]


def test_gemini_can_run_without_ollama(monkeypatch, transport):
    """Verify Gemini can run without Ollama."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("OLLAMA_MODEL")
    transport(lambda _: gemini_response("Gemini story"))
    assert asyncio.run(providers.generate("Write", "Facts")) == "Gemini story"


@pytest.mark.parametrize("response", [
    {"promptFeedback": {"blockReason": "SAFETY"}},
    {"candidates": [{"finishReason": "SAFETY"}]},
])
def test_provider_refusals_are_not_bypassed(monkeypatch, transport, response):
    """Verify provider refusals are not bypassed."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    seen = []
    def handle(request):
        """Act as the upstream transport for the response or error under test."""
        seen.append(request.url.host)
        return httpx.Response(200, json=response)
    transport(handle)
    with pytest.raises(providers.GenerationError, match="declined"):
        asyncio.run(providers.generate("Write", "Facts"))
    assert seen == ["generativelanguage.googleapis.com"]


def test_both_providers_fail_without_leaking_keys_or_upstream_body(monkeypatch, transport, caplog):
    """Verify both providers fail without leaking keys or upstream body."""
    monkeypatch.setenv("GEMINI_API_KEY", "private-gemini-key")
    monkeypatch.setenv("OLLAMA_API_KEY", "private-ollama-key")
    transport(lambda _: httpx.Response(503, text="private upstream body"))
    with pytest.raises(HTTPException) as error:
        asyncio.run(generate_json(AnswerDraft, "Answer", {}))
    assert error.value.status_code == 503
    assert "Gemini" in error.value.detail and "Ollama" in error.value.detail
    exposed = error.value.detail + caplog.text
    assert all(value not in exposed for value in ["private-gemini-key", "private-ollama-key", "private upstream body"])


def test_no_provider_configuration(monkeypatch):
    """Verify no provider configuration."""
    monkeypatch.delenv("OLLAMA_MODEL")
    with pytest.raises(providers.GenerationError, match="GEMINI_API_KEY"):
        asyncio.run(providers.generate("Write", "Facts"))


@pytest.mark.parametrize("base", ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"])
def test_vercel_rejects_local_ollama_before_network_call(monkeypatch, transport, base):
    """Verify vercel rejects local Ollama before network call."""
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("OLLAMA_BASE_URL", base)
    transport(lambda _: pytest.fail("A production request must not target localhost"))
    with pytest.raises(providers.GenerationError, match="hosted OLLAMA_BASE_URL"):
        asyncio.run(providers.generate("Write", "Facts"))


def test_hosted_ollama_auth_is_separate_from_gemini_key(monkeypatch, transport):
    """Verify hosted Ollama auth is separate from Gemini key."""
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://model.example.org")
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-token")
    def handle(request):
        """Act as the upstream transport for the response or error under test."""
        if request.url.host == "generativelanguage.googleapis.com":
            assert "Authorization" not in request.headers
            return httpx.Response(429)
        assert str(request.url) == "https://model.example.org/api/chat"
        assert request.headers["Authorization"] == "Bearer ollama-token"
        assert "x-goog-api-key" not in request.headers
        return httpx.Response(200, json={"message": {"content": "Fallback story"}})
    transport(handle)
    assert asyncio.run(providers.generate("Write", "Facts")) == "Fallback story"


def test_story_graph_uses_gemini_and_cleans_text(monkeypatch, transport):
    """Verify story graph uses Gemini and cleans text."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    def handle(request):
        """Act as the upstream transport for the response or error under test."""
        body = json.loads(request.content)
        assert "responseMimeType" not in body["generationConfig"]
        context = json.loads(body["contents"][0]["parts"][0]["text"])
        assert context["reference_text"] == MONUMENT["reference_text"]
        return gemini_response(r"Known history.\nMore facts.")
    transport(handle)
    story = asyncio.run(generate_story(MONUMENT))
    assert story.story == "Known history. More facts."


def test_story_graph_uses_ollama_after_gemini_failure(monkeypatch, transport):
    """Verify story graph uses Ollama after Gemini failure."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    def handle(request):
        """Act as the upstream transport for the response or error under test."""
        if request.url.host == "generativelanguage.googleapis.com": return httpx.Response(503)
        return httpx.Response(200, json={"message": {"content": "Fallback narration"}})
    transport(handle)
    story = asyncio.run(generate_story(MONUMENT))
    assert story.story == "Fallback narration"


def test_story_errors_are_reported_without_saving(monkeypatch, transport):
    """Verify story errors are reported without saving."""
    transport(lambda _: httpx.Response(503))
    with pytest.raises(AgentError, match="Ollama"):
        asyncio.run(generate_story(MONUMENT))


def test_thought_parts_do_not_appear_in_story(monkeypatch, transport):
    """Verify thought parts do not appear in story."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    transport(lambda _: gemini_response("unused", content={"parts": [
        {"thought": True, "text": "Internal reasoning"}, {"text": "Public narration"}]}))
    assert asyncio.run(providers.generate("Write", "Facts")) == "Public narration"


def test_story_cache_changes_with_provider_and_models_not_api_key(monkeypatch):
    """Verify story cache changes with provider and models not api key."""
    initial = story_key_for(MONUMENT)
    monkeypatch.setenv("GEMINI_API_KEY", "first-private-key")
    primary = story_key_for(MONUMENT)
    assert primary != initial
    monkeypatch.setenv("GEMINI_API_KEY", "rotated-private-key")
    assert story_key_for(MONUMENT) == primary
    monkeypatch.setenv("GEMINI_MODEL", "different-gemini-model")
    assert story_key_for(MONUMENT) != primary
    assert "private" not in json.dumps(providers.generation_config())



def test_existing_ollama_cache_keys_remain_compatible_without_gemini_key():
    """Verify existing Ollama cache keys remain compatible without Gemini key."""
    import hashlib
    context = json.dumps(MONUMENT, sort_keys=True, ensure_ascii=False)
    legacy = hashlib.sha256(f"v1|test-model|{context}".encode()).hexdigest()
    assert story_key_for(MONUMENT) == legacy
