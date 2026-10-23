"""Regression checks use memory only; no Atlas user records are changed."""

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ConnectionFailure, DuplicateKeyError

from virasatguide_backend.main import app
from virasatguide_backend.data import database
from virasatguide_backend.data.security import ALGORITHM, get_password_hash, verify_password


class Users:
    def __init__(self):
        self.documents = {}

    async def find_one(self, query):
        return copy.deepcopy(self.documents.get(query['username']))

    async def insert_one(self, document):
        if document['username'] in self.documents:
            raise DuplicateKeyError('duplicate')
        self.documents[document['username']] = dict(document, _id='test-id')

    async def update_one(self, query, update):
        for document in self.documents.values():
            if document['_id'] == query['_id']:
                document['token_version'] = document.get('token_version', 1) + 1


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('JWT_SECRET_KEY', 'test-only-signing-secret-at-least-32-characters')
    users = Users()
    app.dependency_overrides[database.get_users_collection] = lambda: users
    try:
        yield TestClient(app), users
    finally:
        app.dependency_overrides.clear()


def test_register_login_profile_logout(client):
    api, users = client
    credentials = {'username': 'alice', 'password': 'test-password'}
    assert api.post('/auth/register', json=credentials).status_code == 201
    assert users.documents['alice']['password'] != credentials['password']
    assert api.post('/auth/register', json=credentials).status_code == 400
    response = api.post('/auth/login', data=credentials)
    assert response.status_code == 200
    headers = {'Authorization': 'Bearer ' + response.json()['access_token']}
    profile = api.get('/auth/me', headers=headers)
    assert profile.json() == {'_id': 'test-id', 'username': 'alice'}
    assert 'password' in users.documents['alice']
    assert api.post('/auth/logout', headers=headers).status_code == 200
    assert api.get('/auth/me', headers=headers).status_code == 401


def test_legacy_token_version_and_bad_password(client):
    api, users = client
    users.documents['legacy'] = {'_id': 'test-id', 'username': 'legacy', 'password': get_password_hash('test-password')}
    assert api.post('/auth/login', data={'username': 'legacy', 'password': 'wrong'}).status_code == 401
    token = api.post('/auth/login', data={'username': 'legacy', 'password': 'test-password'}).json()['access_token']
    headers = {'Authorization': 'Bearer ' + token}
    assert api.get('/auth/me', headers=headers).status_code == 200
    assert api.post('/auth/logout', headers=headers).status_code == 200
    assert users.documents['legacy']['token_version'] == 2
    assert api.get('/auth/me', headers=headers).status_code == 401


def test_invalid_expired_and_incomplete_tokens(client, monkeypatch):
    api, _ = client
    secret = 'test-only-signing-secret-at-least-32-characters'
    for payload in [
        {'sub': 'alice', 'version': 1},
        {'sub': 'alice', 'version': 1, 'exp': datetime.now(timezone.utc) - timedelta(seconds=1)},
        {'sub': 'alice', 'version': '1', 'exp': datetime.now(timezone.utc) + timedelta(minutes=1)},
    ]:
        token = jwt.encode(payload, secret, algorithm=ALGORITHM)
        assert api.get('/auth/me', headers={'Authorization': 'Bearer ' + token}).status_code == 401
    assert api.get('/auth/me', headers={'Authorization': 'Bearer invalid'}).status_code == 401
    assert api.get('/auth/me').status_code == 401
    assert api.post('/auth/register', json={'username': ' ', 'password': 'short'}).status_code == 422
    assert not verify_password('anything', 'not-a-valid-hash')


def test_registration_race(client, monkeypatch):
    api, users = client
    monkeypatch.setattr(users, 'find_one', AsyncMock(return_value=None))
    monkeypatch.setattr(users, 'insert_one', AsyncMock(side_effect=DuplicateKeyError('duplicate')))
    assert api.post('/auth/register', json={'username': 'alice', 'password': 'test-password'}).status_code == 400


@pytest.mark.parametrize('failure', [False, True])
def test_database_lifecycle(monkeypatch, failure):
    import asyncio
    monkeypatch.setenv('MONGODB_URI', 'mongodb://localhost')
    monkeypatch.setenv('JWT_SECRET_KEY', 'test-only-signing-secret-at-least-32-characters')
    fake_db = SimpleNamespace(users=SimpleNamespace(create_index=AsyncMock()))
    class Client:
        admin = SimpleNamespace(command=AsyncMock(side_effect=ConnectionFailure('offline') if failure else None))
        close = AsyncMock()
        def __getitem__(self, name):
            return fake_db
    client = Client()
    monkeypatch.setattr(database, 'AsyncMongoClient', lambda *a, **k: client)
    app_stub = SimpleNamespace(state=SimpleNamespace())
    async def check():
        if failure:
            with pytest.raises(ConnectionFailure):
                async with database.lifespan(app_stub):
                    pass
        else:
            async with database.lifespan(app_stub):
                assert app_stub.state.database is fake_db
            fake_db.users.create_index.assert_awaited_once_with('username', unique=True)
        client.close.assert_awaited_once()
        assert app_stub.state.database is None
    asyncio.run(check())
