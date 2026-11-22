"""Agent and endpoint checks use fake search, Ollama, and database services."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from virasatguide_ai_agent import MonumentContext, StoryResult, generate_story
import virasatguide_ai_agent.graph as story_graph
from virasatguide_ai_agent.graph import AgentError
from virasatguide_ai_agent.models import Source
from virasatguide_ai_agent.tools import search_monument
from virasatguide_backend.data.database import get_database, get_users_collection
from virasatguide_backend.main import app
from virasatguide_backend.routes.auth import get_current_user

MONUMENT = {
    "id": 7, "name": "India Gate", "city": "New Delhi",
    "location_tag": "Delhi, India", "cover_image": "https://example.org/gate.jpg",
    "reference_text": "A war memorial designed by Sir Edwin Lutyens.",
}


class MemoryStories:
    def __init__(self):
        self.documents = {}

    async def find_one(self, query):
        return self.documents.get(query["story_key"])

    async def update_one(self, query, update, upsert=False):
        self.documents.setdefault(query["story_key"], update["$setOnInsert"])


def fake_database():
    return SimpleNamespace(
        monuments=SimpleNamespace(find_one=AsyncMock(return_value=MONUMENT)),
        tour_stories=MemoryStories(),
    )


def test_agent_validates_monument_and_story():
    with pytest.raises(ValidationError):
        MonumentContext.model_validate({**MONUMENT, "reference_text": "  "})
    with pytest.raises(ValidationError):
        MonumentContext.model_validate({**MONUMENT, "unknown": "value"})
    with pytest.raises(AgentError):
        asyncio.run(generate_story({"id": 7}))


@pytest.fixture
def story_tools(monkeypatch):
    generate = AsyncMock(return_value="Known history.")
    monkeypatch.setattr(story_graph, "generate", generate)
    monkeypatch.setattr(story_graph, "search_monument", lambda *_: [])
    return generate


def test_graph_searches_then_writes_using_all_monument_fields(story_tools, monkeypatch):
    source = Source(title="India Gate history", url="https://example.org/history",
                    snippet="India Gate is in New Delhi.")
    search = lambda name, city: [source] if (name, city) == ("India Gate", "New Delhi") else []
    monkeypatch.setattr(story_graph, "search_monument", search)
    story_tools.return_value = "A vivid story about India Gate."
    result = asyncio.run(generate_story(MONUMENT))
    assert result.monument_id == 7
    assert result.story == "A vivid story about India Gate."
    assert result.research_status == "web_results"
    assert result.sources == [source]
    context = json.loads(story_tools.call_args.args[1])
    for field, value in MONUMENT.items():
        assert context[field] == value


def test_graph_uses_database_when_search_has_no_results(story_tools):
    result = asyncio.run(generate_story(MONUMENT))
    assert result.research_status == "database_only"
    assert result.sources == []


def test_graph_rejects_empty_model_output(story_tools):
    story_tools.return_value = "  "
    with pytest.raises(AgentError, match="empty story"):
        asyncio.run(generate_story(MONUMENT))


def test_search_prefers_duckduckgo_and_falls_back_to_bing(monkeypatch):
    import virasatguide_ai_agent.tools as tools
    from ddgs.exceptions import DDGSException
    engines = []
    class FakeDDGS:
        def __init__(self, timeout):
            assert timeout == 8
        def text(self, query, **kwargs):
            engines.append(kwargs["backend"])
            if kwargs["backend"] == "duckduckgo":
                raise DDGSException("no results")
            return [
                {"title": "India Gate", "href": "http://example.org/unsafe", "body": "New Delhi"},
                {"title": "Other site", "href": "https://example.org/other", "body": "New Delhi"},
                {"title": "India Gate history", "href": "https://example.org/gate", "body": "New Delhi memorial"},
            ]
    monkeypatch.setattr(tools, "DDGS", FakeDDGS)
    sources = search_monument("India Gate", "New Delhi")
    assert engines == ["duckduckgo", "bing"]
    assert len(sources) == 1 and str(sources[0].url) == "https://example.org/gate"


def test_story_endpoint_auth_success_and_missing_monument(monkeypatch):
    import virasatguide_backend.services.stories as service
    db = fake_database()
    generated = AsyncMock(return_value=StoryResult(
        monument_id=7, story="A short story.", sources=[], research_status="database_only"
    ))
    monkeypatch.setattr(service, "generate_story", generated)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_users_collection] = lambda: object()
    try:
        api = TestClient(app)
        assert api.post("/api/v1/monuments/7/story").status_code == 401
        app.dependency_overrides[get_current_user] = lambda: {"username": "test"}
        assert api.post("/api/v1/monuments/0/story").status_code == 422
        response = api.post("/api/v1/monuments/7/story")
        assert response.status_code == 200
        assert response.json()["story"] == "A short story."
        generated.assert_awaited_once_with(MONUMENT)
        assert len(db.tour_stories.documents) == 1
        saved = next(iter(db.tour_stories.documents.values()))
        assert saved["result"]["story"] == "A short story."
        assert saved["monument_id"] == 7
        assert api.post("/api/v1/monuments/7/story").json()["story"] == "A short story."
        generated.assert_awaited_once()
        db.monuments.find_one.return_value = {**MONUMENT, "reference_text": "Updated history."}
        assert api.post("/api/v1/monuments/7/story").status_code == 200
        assert generated.await_count == 2
        assert len(db.tour_stories.documents) == 2
        db.monuments.find_one.return_value = None
        assert api.post("/api/v1/monuments/999/story").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_story_endpoint_accepts_signed_bearer_token(monkeypatch):
    import virasatguide_backend.services.stories as service
    from test_auth import Users
    from virasatguide_backend.data.security import create_access_token

    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-signing-secret-at-least-32-characters")
    users = Users()
    users.documents["alice"] = {"_id": "test-id", "username": "alice", "token_version": 1}
    db = fake_database()
    monkeypatch.setattr(service, "generate_story", AsyncMock(return_value=StoryResult(
        monument_id=7, story="A short story.", sources=[], research_status="database_only"
    )))
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_users_collection] = lambda: users
    try:
        token = create_access_token({"sub": "alice", "version": 1})
        response = TestClient(app).post(
            "/api/v1/monuments/7/story", headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["story"] == "A short story."
        spec = app.openapi()["paths"]["/api/v1/monuments/{monument_id}/story"]["post"]
        assert "401" in spec["responses"]
    finally:
        app.dependency_overrides.clear()



def test_story_text_removes_literal_escapes_and_newlines(story_tools):
    story_tools.return_value = r'"First line\nSecond\tline"'
    result = asyncio.run(generate_story(MONUMENT))
    assert result.story == "First line Second line"
    assert "\n" not in result.story and "\\n" not in result.story


def test_story_text_removes_real_line_breaks_and_controls(story_tools):
    story_tools.return_value = "First line\nSecond\tline\x1b"
    result = asyncio.run(generate_story(MONUMENT))
    assert result.story == "First line Second line"
