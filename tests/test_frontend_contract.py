"""Opt-in actual frontend JS wrappers + FastAPI + local Mongo; external services simulated."""

import asyncio
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.responses import RedirectResponse
from pymongo import AsyncMongoClient
import uvicorn

from virasatguide_backend.main import app
from virasatguide_backend.data.database import lifespan
from virasatguide_backend.routes import google_oauth
from virasatguide_backend.services import stories
from virasatguide_backend.travel import capabilities, planner, retrieval
from virasatguide_backend.travel.schemas import AnswerDraft
from test_travel import draft, STORY, MONUMENT

FRONTEND = os.getenv('FRONTEND_CONTRACT_DIR', '')
URI = os.getenv('MONGODB_TEST_URI', '')
pytestmark = pytest.mark.skipif(not FRONTEND or not URI.startswith('mongodb://127.0.0.1:'), reason='Requires frontend snapshot and isolated local Mongo')


def test_actual_frontend_wrappers_with_real_mongo(monkeypatch):
    database_name = 'virasatguide_frontend_test_' + uuid4().hex
    monkeypatch.setenv('MONGODB_URI', URI)
    monkeypatch.setenv('MONGODB_DB', database_name)
    monkeypatch.setenv('FRONTEND_URL', 'http://localhost:5173/src/dashboard/index.html')
    monkeypatch.setenv('GOOGLE_REDIRECT_URI', 'http://127.0.0.1:8000/auth/google/callback')
    monkeypatch.setenv('FISH_AUDIO_API_KEY', 'contract-test-not-a-real-key')
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'contract-test-client')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'contract-test-secret')
    @asynccontextmanager
    async def test_lifespan(application):
        async with lifespan(application):
            await application.state.database.monuments.insert_one({**MONUMENT,'location_tag':'Uttar Pradesh, India','cover_image':'https://example.org/taj.jpg'})
            yield
    monkeypatch.setattr(app.router, 'lifespan_context', test_lifespan)
    google = SimpleNamespace(
        authorize_redirect=AsyncMock(return_value=RedirectResponse('https://accounts.google.com/fixture',status_code=302)),
        authorize_access_token=AsyncMock(return_value={'userinfo':{'sub':'contract-subject','email':'contract@example.org','email_verified':True,'name':'Contract Traveler'}}))
    app.dependency_overrides[google_oauth.get_google_client] = lambda: google
    monkeypatch.setattr(stories, 'generate_story', AsyncMock(return_value=STORY))
    monkeypatch.setattr(planner, 'search_destination', lambda _: [])
    generate = AsyncMock(return_value=draft())
    monkeypatch.setattr(planner, 'generate_json', generate)
    async def answer(schema, instruction, context):
        await asyncio.sleep(0.02)  # Let concurrent questions read the same version.
        if 'weather' in context['question']:
            return AnswerDraft(answer='Not enough evidence',supported=False,evidence_ids=[])
        return AnswerDraft(answer='Shah Jahan commissioned it.',supported=True,evidence_ids=['reference-1'])
    monkeypatch.setattr(retrieval, 'generate_json', answer)
    monkeypatch.setattr(capabilities, 'ai_ready', AsyncMock(return_value=True))
    original_client = httpx.AsyncClient
    def upstream(request):
        if request.url.host == 'api.fish.audio':
            return httpx.Response(200,content=b'ID3'+b'fixture-audio'*100,headers={'content-type':'audio/mpeg'})
        if request.url.host == 'geocoding-api.open-meteo.com':
            return httpx.Response(200,json={'results':[{'id':1269515,'name':'Jaipur','admin1':'Rajasthan','country':'India','country_code':'IN','latitude':26.91962,'longitude':75.78781}]})
        raise AssertionError('Unexpected external request')
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs: original_client(transport=httpx.MockTransport(upstream),**kwargs))
    # Bind an available local port before starting the server.
    listener = socket.socket()
    listener.bind(('127.0.0.1',0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]),daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started, 'Local contract server did not start'
        script = Path(__file__).with_name('frontend_contract.mjs')
        result = subprocess.run(['node',str(script),FRONTEND,f'http://127.0.0.1:{port}'],capture_output=True,text=True,timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout)
        assert generate.await_count == 1  # GET/PDF and both sign-in methods reuse the saved plan.
        stories.generate_story.assert_awaited_once()  # Story route and tour session share the cache.
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        app.dependency_overrides.clear()
        async def cleanup():
            async with AsyncMongoClient(URI) as client:
                assert database_name.startswith('virasatguide_frontend_test_')
                await client.drop_database(database_name)
        asyncio.run(cleanup())
