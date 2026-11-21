"""Status routes report dependency failures without exposing connection details."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import PyMongoError

from virasatguide_backend.data.database import get_database
from virasatguide_backend.main import app


@pytest.mark.parametrize('available', [True, False])
def test_database_health_and_ping(available):
    database = SimpleNamespace(command=AsyncMock(return_value={'ok': 1}))
    if not available:
        database.command.side_effect = PyMongoError('private connection details')
    app.dependency_overrides[get_database] = lambda: database
    try:
        api = TestClient(app)
        response = api.get('/health')
        assert response.status_code == (200 if available else 503)
        assert response.json()['database'] == ('connected' if available else 'disconnected')
        assert response.headers['cache-control'] == 'no-store'
        assert 'private connection details' not in response.text
        ping = api.get('/ping-db')
        assert ping.status_code == (200 if available else 503)
        assert ping.json()['ok'] == available
        assert 'private connection details' not in ping.text
        assert api.get('/').status_code == 200
    finally:
        app.dependency_overrides.clear()
