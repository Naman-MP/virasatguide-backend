"""Regression checks use memory only; no Atlas user records are changed."""

import copy
import re
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


def matches(document, query):
    """Apply the subset of MongoDB filters that these fixtures exercise."""
    for key, expected in query.items():
        value = document.get(key)
        if key == "$or":
            if not any(matches(document, clause) for clause in expected):
                return False
        elif isinstance(expected, dict):
            if "$exists" in expected and (key in document) != expected["$exists"]:
                return False
            if "$type" in expected and not isinstance(value, str):
                return False
            if "$in" in expected and value not in expected["$in"]:
                return False
            if "$regex" in expected and (not isinstance(value, str) or not re.search(expected["$regex"], value, re.I)):
                return False
        elif value != expected:
            return False
    return True


class Cursor:
    """Model the MongoDB cursor operations needed by account tests."""
    def __init__(self, documents):
        """Initialize the fixture state for the operations used in these tests."""
        self.documents = documents
    def limit(self, count):
        """Bound the number of records returned by the fixture cursor."""
        self.documents = self.documents[:count]
        return self
    async def to_list(self):
        """Collect fixture cursor results."""
        return copy.deepcopy(self.documents)


class Users:
    """Provide an in-memory account store with MongoDB-style matching and uniqueness."""
    def __init__(self):
        """Initialize the fixture state for the operations used in these tests."""
        self.documents = {}
        self.indexes = []

    def find(self, query, projection=None):
        """Return fixture records matching the requested fields."""
        return Cursor([d for d in self.documents.values() if matches(d, query)])

    async def find_one(self, query):
        """Return the matching fixture document when it exists."""
        return next((copy.deepcopy(d) for d in self.documents.values() if matches(d, query)), None)

    def check_unique(self, document):
        """Enforce the same account or offer keys checked by the real store."""
        for other in self.documents.values():
            if other.get('_id') == document.get('_id'):
                continue
            if other['username'] == document['username'] or (
                document.get('login_username_normalized') is not None and
                other.get('login_username_normalized') == document['login_username_normalized']):
                raise DuplicateKeyError('duplicate')

    async def insert_one(self, document):
        """Persist a test document in the fixture store."""
        document = dict(document)
        document.setdefault('_id', 'test-id' if not self.documents else 'test-id-' + str(len(self.documents)))
        self.check_unique(document)
        self.documents[document['username']] = document

    async def create_index(self, field, **options):
        """Record which indexes the application attempts to create."""
        self.indexes.append((field, options))

    async def update_one(self, query, update):
        """Apply the subset of update operators required by the test."""
        for document in self.documents.values():
            if matches(document, query):
                if isinstance(update, list):
                    document['token_version'] = document.get('token_version', 1) + 1
                else:
                    candidate = {**document, **update['$set']}
                    self.check_unique(candidate)
                    document.update(update['$set'])
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def find_one_and_update(self, query, update, **kwargs):
        """Apply the fixture's atomic update and return the stored winner."""
        for document in self.documents.values():
            if matches(document, query):
                candidate = {**document, **update['$set']}
                self.check_unique(candidate)
                document.update(update['$set'])
                return copy.deepcopy(document)
        return None


@pytest.fixture
def client(monkeypatch):
    """Provide the FastAPI test client and isolated state for this module."""
    monkeypatch.setenv('JWT_SECRET_KEY', 'test-only-signing-secret-at-least-32-characters')
    users = Users()
    app.dependency_overrides[database.get_users_collection] = lambda: users
    try:
        yield TestClient(app), users
    finally:
        app.dependency_overrides.clear()


def test_register_login_profile_logout(client):
    """Verify register login profile logout."""
    api, users = client
    credentials = {'username': 'alice', 'password': 'test-password'}
    assert api.post('/auth/register', json=credentials).status_code == 201
    assert users.documents['alice']['password'] != credentials['password']
    assert api.post('/auth/register', json=credentials).status_code == 400
    response = api.post('/auth/login', data=credentials)
    assert response.status_code == 200
    headers = {'Authorization': 'Bearer ' + response.json()['access_token']}
    profile = api.get('/auth/me', headers=headers)
    assert profile.json() == {'_id': 'test-id', 'username': 'alice', 'full_name': None, 'date_of_birth': None, 'gender': 'prefer_not_to_say', 'onboarding_completed': False, 'name': None, 'email': None, 'google_sub': None, 'login_username': 'alice', 'credentials_set': True}
    assert 'password' in users.documents['alice']
    assert api.post('/auth/logout', headers=headers).status_code == 200
    assert api.get('/auth/me', headers=headers).status_code == 401


def test_legacy_token_version_and_bad_password(client):
    """Verify legacy token version and bad password."""
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
    """Verify invalid expired and incomplete tokens."""
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
    """Verify registration race."""
    api, users = client
    monkeypatch.setattr(users, 'find_one', AsyncMock(return_value=None))
    monkeypatch.setattr(users, 'insert_one', AsyncMock(side_effect=DuplicateKeyError('duplicate')))
    assert api.post('/auth/register', json={'username': 'alice', 'password': 'test-password'}).status_code == 400


@pytest.mark.parametrize('failure', [False, True])
def test_database_lifecycle(monkeypatch, failure):
    """Verify database lifecycle."""
    import asyncio
    monkeypatch.setenv('MONGODB_URI', 'mongodb://localhost')
    monkeypatch.setenv('JWT_SECRET_KEY', 'test-only-signing-secret-at-least-32-characters')
    fake_db = SimpleNamespace(users=Users(), monuments=SimpleNamespace(create_index=AsyncMock()), tour_stories=SimpleNamespace(create_index=AsyncMock()), travel_offers=SimpleNamespace(create_index=AsyncMock()))
    class Client:
        """Represent a MongoDB client that exposes only the startup operations under test."""
        admin = SimpleNamespace(command=AsyncMock(side_effect=ConnectionFailure('offline') if failure else None))
        close = AsyncMock()
        def __getitem__(self, name):
            """Resolve the fixture database by its requested name."""
            return fake_db
    client = Client()
    monkeypatch.setattr(database, 'AsyncMongoClient', lambda *a, **k: client)
    app_stub = SimpleNamespace(state=SimpleNamespace())
    async def check():
        """Run the database lifecycle with the configured success or failure fixture."""
        if failure:
            with pytest.raises(ConnectionFailure):
                async with database.lifespan(app_stub):
                    pass
        else:
            async with database.lifespan(app_stub):
                assert app_stub.state.database is fake_db
            assert fake_db.users.indexes == [('username', {'unique': True}), ('login_username_normalized', {'unique': True, 'name': 'unique_login_username_normalized', 'partialFilterExpression': {'login_username_normalized': {'$type': 'string'}}})]
            fake_db.monuments.create_index.assert_awaited_once_with('id', unique=True)
            fake_db.tour_stories.create_index.assert_awaited_once_with('story_key', unique=True)
            fake_db.travel_offers.create_index.assert_awaited_once_with('expires_at', expireAfterSeconds=0)
        client.close.assert_awaited_once()
        assert app_stub.state.database is None
    asyncio.run(check())
