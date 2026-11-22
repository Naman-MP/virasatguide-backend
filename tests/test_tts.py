"""The audio route reads saved stories and sends only their text to Fish Audio."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi.testclient import TestClient

from virasatguide_backend.data.database import get_database, get_users_collection
from virasatguide_backend.data.stories import story_key_for
from virasatguide_backend.main import app
from virasatguide_backend.routes.auth import get_current_user

MONUMENT = {"id": 7, "name": "India Gate", "city": "New Delhi"}
STORY = {"monument_id": 7, "story": "A vivid monument story.",
         "sources": [], "research_status": "database_only"}
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00audio"


class FakeFishClient:
    """Supply deterministic audio bytes and record the outgoing Fish Audio request."""
    def __init__(self, response=None, error=None):
        """Initialize the fixture state for the operations used in these tests."""
        self.response = response
        self.error = error
        self.calls = []

    async def __aenter__(self):
        """Return the fake HTTP client used inside the async context."""
        return self

    async def __aexit__(self, *args):
        """Exit the fake HTTP context after the request."""
        pass

    async def post(self, url, **kwargs):
        """Capture the synthesis request and return the configured test response."""
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def setup_api(monument=MONUMENT, saved=True):
    """Construct a test API with fixture storage, authentication, and Fish Audio."""
    stories = SimpleNamespace(find_one=AsyncMock(return_value={"result": STORY} if saved else None))
    db = SimpleNamespace(monuments=SimpleNamespace(find_one=AsyncMock(return_value=monument)),
                         tour_stories=stories)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_users_collection] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: {"username": "test"}
    return TestClient(app), db


def test_audio_requires_login_and_valid_id():
    """Verify audio requires login and valid ID."""
    api, _ = setup_api()
    try:
        app.dependency_overrides.pop(get_current_user)
        assert api.post("/api/v1/monuments/7/audio").status_code == 401
        app.dependency_overrides[get_current_user] = lambda: {"username": "test"}
        assert api.post("/api/v1/monuments/0/audio").status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_audio_requires_monument_and_saved_story(monkeypatch):
    """Verify audio requires monument and saved story."""
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "test-key")
    api, db = setup_api(monument=None)
    try:
        assert api.post("/api/v1/monuments/7/audio").status_code == 404
        db.monuments.find_one.return_value = MONUMENT
        db.tour_stories.find_one.return_value = None
        response = api.post("/api/v1/monuments/7/audio")
        assert response.status_code == 404
        db.tour_stories.find_one.assert_awaited_with({"story_key": story_key_for(MONUMENT)})
    finally:
        app.dependency_overrides.clear()


def test_audio_requires_fish_key(monkeypatch):
    """Verify audio requires Fish Audio key."""
    monkeypatch.delenv("FISH_AUDIO_API_KEY", raising=False)
    api, _ = setup_api()
    try:
        assert api.post("/api/v1/monuments/7/audio").status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_audio_returns_mp3_with_free_model_and_optional_voice(monkeypatch):
    """Verify audio returns MP3 with free model and optional voice."""
    import virasatguide_backend.routes.tts as route
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "test-key")
    monkeypatch.setenv("FISH_AUDIO_VOICE_ID", "voice-123")
    fish = FakeFishClient(httpx.Response(200, content=MP3))
    monkeypatch.setattr(route.httpx, "AsyncClient", lambda **kwargs: fish)
    api, _ = setup_api()
    try:
        response = api.post("/api/v1/monuments/7/audio")
        assert response.status_code == 200
        assert response.content == MP3
        assert response.headers["content-type"] == "audio/mpeg"
        assert response.headers["cache-control"] == "private, no-store"
        url, request = fish.calls[0]
        assert url == "https://api.fish.audio/v1/tts"
        assert request["headers"] == {"Authorization": "Bearer test-key", "model": "s2.1-pro-free"}
        assert request["json"] == {"text": STORY["story"], "format": "mp3",
                                   "reference_id": "voice-123"}
    finally:
        app.dependency_overrides.clear()


def test_audio_handles_provider_failure_and_timeout(monkeypatch):
    """Verify audio handles provider failure and timeout."""
    import virasatguide_backend.routes.tts as route
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "test-key")
    monkeypatch.delenv("FISH_AUDIO_VOICE_ID", raising=False)
    fish = FakeFishClient(httpx.Response(401, json={"message": "bad key"}))
    monkeypatch.setattr(route.httpx, "AsyncClient", lambda **kwargs: fish)
    api, _ = setup_api()
    try:
        response = api.post("/api/v1/monuments/7/audio")
        assert response.status_code == 502
        assert "bad key" not in response.text
        assert fish.calls[0][1]["json"] == {"text": STORY["story"], "format": "mp3"}
        fish.response = httpx.Response(200, json={"message": "not audio"})
        assert api.post("/api/v1/monuments/7/audio").status_code == 502
        fish.error = httpx.ReadTimeout("too slow")
        assert api.post("/api/v1/monuments/7/audio").status_code == 504
    finally:
        app.dependency_overrides.clear()


def test_audio_openapi_documents_only_binary_mp3():
    """Verify audio openapi documents only binary MP3."""
    response = app.openapi()["paths"]["/api/v1/monuments/{monument_id}/audio"]["post"]["responses"]["200"]
    assert response["content"] == {"audio/mpeg": {"schema": {"type": "string", "format": "binary"}}}
