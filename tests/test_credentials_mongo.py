"""Opt-in tests of real MongoDB partial uniqueness and atomic first-time setup."""

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from pymongo import AsyncMongoClient

from virasatguide_backend.main import app
from virasatguide_backend.data.credentials import migrate_login_names
from virasatguide_backend.data.database import get_users_collection
from virasatguide_backend.data.security import create_access_token, get_password_hash

URI = os.getenv('MONGODB_TEST_URI', '')
pytestmark = pytest.mark.skipif(not URI.startswith('mongodb://127.0.0.1:'), reason='Requires explicit isolated local MONGODB_TEST_URI')


def test_real_mongo_migration_and_concurrent_credentials():
    async def check():
        client = AsyncMongoClient(URI, serverSelectionTimeoutMS=3000)
        name = 'virasatguide_credentials_test_' + uuid4().hex
        db = client[name]
        users = db.users
        password = 'local-test-password'
        old_hash = get_password_hash(password)
        try:
            await users.create_index('username', unique=True)
            await users.insert_one({'username': 'Legacy.Name', 'password': old_hash, 'token_version': 1})
            for subject in ['first', 'second', 'third']:
                await users.insert_one({'username': 'google:' + subject, 'google_sub': subject, 'token_version': 1})
            before = await users.find_one({'username': 'Legacy.Name'})
            assert (await migrate_login_names(users))['accounts_to_backfill'] == 1
            assert (await migrate_login_names(users, apply=True))['accounts_to_backfill'] == 1
            assert (await migrate_login_names(users, apply=True))['accounts_to_backfill'] == 0
            after = await users.find_one({'username': 'Legacy.Name'})
            assert before['_id'] == after['_id'] and old_hash == after['password']
            indexes = await users.index_information()
            assert indexes['unique_login_username_normalized']['unique'] is True
            assert 'username_1' in indexes
            app.dependency_overrides[get_users_collection] = lambda: users
            def headers(subject):
                return {'Authorization': 'Bearer ' + create_access_token({'sub': 'google:' + subject, 'version': 1})}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as api:
                responses = await asyncio.gather(*[
                    api.put('/auth/credentials', headers=headers(subject), json={'username': login, 'password': password})
                    for subject, login in [('first','Shared.Name'), ('second','SHARED.NAME')]
                ])
                assert sorted(r.status_code for r in responses) == [200, 409]
                assert await users.count_documents({'login_username_normalized':'shared.name'}) == 1
                responses = await asyncio.gather(*[
                    api.put('/auth/credentials', headers=headers('third'), json={'username': login, 'password': password})
                    for login in ['FirstChoice', 'SecondChoice']
                ])
                assert sorted(r.status_code for r in responses) == [200, 409]
                response = await api.post('/auth/login', data={'username':'SHARED.NAME','password':password})
                assert response.status_code == 200
                profile = await api.get('/auth/me', headers={'Authorization':'Bearer '+response.json()['access_token']})
                assert profile.json()['username'] in {'google:first','google:second'}
                assert (await api.post('/auth/login', data={'username':'legacy.name','password':password})).status_code == 200
                # Registration and Google setup share the same unique namespace.
                await users.insert_one({'username':'google:fourth','google_sub':'fourth','token_version':1})
                responses = await asyncio.gather(
                    api.put('/auth/credentials', headers=headers('fourth'), json={'username':'Race.Local','password':password}),
                    api.post('/auth/register', json={'username':'RACE.LOCAL','password':password}),
                )
                assert sorted(r.status_code for r in responses) in ([200,400], [201,409])
                assert await users.count_documents({'login_username_normalized':'race.local'}) == 1
                # Colliding legacy users stay distinct and can use exact-case logins.
                collision = db.collision_test
                await collision.insert_many([{'username':n,'password':old_hash} for n in ['Conflict','CONFLICT']])
                result = await migrate_login_names(collision, apply=True)
                assert result['collision_groups'] == 1 and result['accounts_to_backfill'] == 0
                assert await collision.count_documents({'login_username_normalized': {'$exists': True}}) == 0
                assert 'unique_login_username_normalized' in await collision.index_information()

        finally:
            app.dependency_overrides.clear()
            assert name.startswith('virasatguide_credentials_test_')
            await client.drop_database(name)  # Only this newly created local test database.
            await client.close()
    asyncio.run(check())
