"""Google-linked password setup keeps account identity, ownership and OAuth intact."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import jwt
import pytest

from test_auth import Users, client
from test_google_oauth import google_flow, set_token, start
from virasatguide_backend.data.credentials import migrate_login_names
from virasatguide_backend.data.security import ALGORITHM, create_access_token, get_password_hash, get_secret_key, verify_password

PASSWORD = 'a-test-password'


def headers(username='google:12345'):
    return {'Authorization': 'Bearer ' + create_access_token({'sub': username, 'version': 1})}


def add_google(users, subject='12345'):
    key = 'google:' + subject
    users.documents[key] = {'_id': subject, 'username': key, 'google_sub': subject,
                            'token_version': 1, 'onboarding_completed': False}
    return users.documents[key]


def test_google_credentials_keep_identity_and_password_login(google_flow):
    api, users, google, key = google_flow
    query = start(api, 'continue')
    set_token(google, key, query)
    response = api.get('/auth/google/callback', params={'state': query['state'][0], 'code': 'verified'})
    old_headers = {'Authorization': 'Bearer ' + response.json()['access_token']}
    before = api.get('/auth/me', headers=old_headers).json()
    assert before['credentials_set'] is False and before['login_username'] is None
    response = api.put('/auth/credentials', json={'username': 'Traveler.One', 'password': PASSWORD}, headers=old_headers)
    assert response.status_code == 200, response.text
    profile = response.json()
    assert profile['_id'] == before['_id'] and profile['username'] == 'google:12345'
    assert profile['login_username'] == 'Traveler.One' and profile['credentials_set'] is True
    assert profile['onboarding_completed'] is False
    stored = users.documents['google:12345']
    assert verify_password(PASSWORD, stored['password']) and stored['password'].startswith('$argon2')
    assert stored['password'] not in response.text and PASSWORD not in response.text
    assert response.headers['cache-control'] == 'private, no-store'
    tokens = []
    for name in ['traveler.one', 'TRAVELER.ONE', 'Traveler.One']:
        result = api.post('/auth/login', data={'username': name, 'password': PASSWORD})
        assert result.status_code == 200
        assert set(result.json()) == {'access_token', 'token_type'}
        token = result.json()['access_token']
        tokens.append(token)
        assert jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])['sub'] == 'google:12345'
        assert api.get('/auth/me', headers={'Authorization': 'Bearer ' + token}).json() == profile
    assert len(set(tokens)) == 3
    assert api.get('/auth/me', headers=old_headers).json() == profile
    assert len(users.documents) == 1
    query = start(api, 'login')
    set_token(google, key, query)
    response = api.get('/auth/google/callback', params={'state': query['state'][0], 'code': 'verified'})
    assert response.status_code == 200
    token = response.json()['access_token']
    assert api.get('/auth/me', headers={'Authorization': 'Bearer ' + token}).json() == profile
    assert len(users.documents) == 1
    assert api.put('/auth/credentials', headers=old_headers,
                   json={'username': 'Replacement', 'password': 'different-password'}).status_code == 409
    assert verify_password(PASSWORD, stored['password'])


def test_setup_auth_ownership_origin_and_private_fields(client):
    api, users = client
    first = add_google(users)
    second = add_google(users, 'second')
    body = {'username': 'OnlyMine', 'password': PASSWORD}
    assert api.put('/auth/credentials', json=body).status_code == 401
    users.documents['local'] = {'_id': 'local', 'username': 'local', 'password': get_password_hash(PASSWORD)}
    assert api.put('/auth/credentials', json=body, headers=headers('local')).status_code == 403
    assert api.put('/auth/credentials', json={**body, 'user_id': 'second'}, headers=headers()).status_code == 422
    api.cookies.set('virasat_access_token', headers()['Authorization'][7:], domain='testserver.local', path='/')
    assert api.put('/auth/credentials', json=body).status_code == 403
    assert api.put('/auth/credentials', json=body, headers={'Origin': 'https://attacker.example'}).status_code == 403
    assert api.put('/auth/credentials', json=body, headers={'Origin': 'http://localhost:5173'}).status_code == 200
    assert first['login_username'] == 'OnlyMine'
    assert not second.get('password') and not second.get('login_username')
    assert api.get('/auth/me').json()['credentials_set'] is True
    preflight = api.options('/auth/credentials', headers={'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'PUT', 'Access-Control-Request-Headers': 'Content-Type'})
    assert preflight.status_code == 200 and preflight.headers['access-control-allow-credentials'] == 'true'


@pytest.mark.parametrize('username', ['ab', 'x'*33, 'two words', 'google:123', 'GOOGLE:123', 'éclair', 'user@host', 'newline\n', ' user', 'user ', '$where'])
def test_username_validation_and_no_password_echo(client, username):
    api, users = client
    add_google(users)
    for path in ['/auth/credentials', '/auth/register']:
        response = getattr(api, 'put' if path.endswith('credentials') else 'post')(
            path, json={'username': username, 'password': PASSWORD}, headers=headers())
        assert response.status_code == 422
        assert PASSWORD not in response.text and 'input' not in response.text


@pytest.mark.parametrize('password', ['short', 'x'*129, 12345678, None, {'secret': 'value'}])
def test_password_validation_does_not_echo_input(client, password):
    api, users = client
    add_google(users)
    response = api.put('/auth/credentials', json={'username': 'Traveler', 'password': password}, headers=headers())
    assert response.status_code == 422
    assert 'input' not in response.text and json.dumps(password) not in response.text


@pytest.mark.parametrize('name,password', [('unknown', PASSWORD), ('Traveler', 'wrong')])
def test_invalid_login_is_generic(client, name, password):
    api, users = client
    add_google(users)
    assert api.put('/auth/credentials', json={'username': 'Traveler', 'password': PASSWORD}, headers=headers()).status_code == 200
    response = api.post('/auth/login', data={'username': name, 'password': password})
    assert response.status_code == 401
    assert response.json() == {'detail': 'Incorrect username or password'}
    assert api.post('/auth/login', data={'username': 'google:12345', 'password': PASSWORD}).status_code == 401


def test_legacy_names_are_reserved_and_login_is_case_insensitive(client):
    api, users = client
    users.documents['Legacy.Name'] = {'_id': 'old-id', 'username': 'Legacy.Name', 'password': get_password_hash(PASSWORD)}
    add_google(users)
    body = {'username': 'legacy.name', 'password': PASSWORD}
    assert api.post('/auth/register', json=body).status_code == 400
    assert api.put('/auth/credentials', json=body, headers=headers()).status_code == 409
    login = api.post('/auth/login', data=body)
    assert login.status_code == 200
    profile = api.get('/auth/me', headers={'Authorization': 'Bearer ' + login.json()['access_token']}).json()
    assert profile['username'] == profile['login_username'] == 'Legacy.Name'
    assert profile['credentials_set'] is True
    assert api.post('/auth/register', json={'username': 'New.User', 'password': PASSWORD}).status_code == 201
    assert api.post('/auth/register', json={'username': 'NEW.USER', 'password': PASSWORD}).status_code == 400
    assert api.post('/auth/login', data={'username': 'new.user', 'password': PASSWORD}).status_code == 200


def test_two_setup_requests_for_same_account_are_atomic(client):
    api, users = client
    add_google(users)
    def attempt(name):
        return api.put('/auth/credentials', json={'username': name, 'password': PASSWORD}, headers=headers()).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ['FirstName', 'SecondName']))
    assert sorted(results) == [200, 409]
    assert users.documents['google:12345']['login_username'] in {'FirstName', 'SecondName'}


def test_concurrent_case_insensitive_name_claims(client):
    api, users = client
    add_google(users)
    add_google(users, 'second')
    def attempt(account_and_name):
        account, name = account_and_name
        return api.put('/auth/credentials', json={'username': name, 'password': PASSWORD}, headers=headers(account)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [('google:12345', 'Shared.Name'), ('google:second', 'SHARED.NAME')]))
    assert sorted(results) == [200, 409]
    assert sum(bool(u.get('password')) for u in users.documents.values()) == 1


def test_migration_is_preflighted_idempotent_and_keeps_identity():
    users = Users()
    password_hash = get_password_hash(PASSWORD)
    users.documents['Old.Name'] = {'_id': 'stable-id', 'username': 'Old.Name', 'password': password_hash}
    add_google(users)
    result = asyncio.run(migrate_login_names(users))
    assert result == {'accounts_checked': 2, 'accounts_to_backfill': 1, 'collision_groups': 0, 'applied': False}
    assert 'login_username' not in users.documents['Old.Name'] and not users.indexes
    assert asyncio.run(migrate_login_names(users, apply=True))['accounts_to_backfill'] == 1
    assert asyncio.run(migrate_login_names(users, apply=True))['accounts_to_backfill'] == 0
    assert users.documents['Old.Name']['username'] == 'Old.Name'
    assert users.documents['Old.Name']['_id'] == 'stable-id'
    assert users.documents['Old.Name']['password'] == password_hash
    assert users.documents['Old.Name']['login_username_normalized'] == 'old.name'
    assert 'password' not in users.documents['google:12345']
    assert users.indexes[0][1]['partialFilterExpression'] == {'login_username_normalized': {'$type': 'string'}}


def test_migration_skips_colliding_legacy_accounts():
    users = Users()
    for name in ['Old.Name', 'OLD.NAME']:
        users.documents[name] = {'_id': name, 'username': name, 'password': 'unchanged-hash'}
    result = asyncio.run(migrate_login_names(users, apply=True))
    assert result['collision_groups'] == 1 and result['accounts_to_backfill'] == 0
    assert all('login_username_normalized' not in u for u in users.documents.values())
    assert users.indexes[0][0] == 'login_username_normalized'


def test_ambiguous_legacy_login_requires_exact_case(client):
    api, users = client
    for name in ['Legacy', 'LEGACY']:
        users.documents[name] = {'_id': name, 'username': name, 'password': get_password_hash(PASSWORD)}
    assert api.post('/auth/login', data={'username': 'legacy', 'password': PASSWORD}).status_code == 401
    for name in ['Legacy', 'LEGACY']:
        token = api.post('/auth/login', data={'username': name, 'password': PASSWORD}).json()['access_token']
        assert api.get('/auth/me', headers={'Authorization': 'Bearer '+token}).json()['username'] == name


def test_no_email_linking_and_no_google_password_before_setup(google_flow):
    api, users, google, key = google_flow
    users.documents['alice'] = {'_id': 'local', 'username': 'alice', 'password': get_password_hash(PASSWORD), 'email': 'alice@example.com'}
    query = start(api, 'continue')
    set_token(google, key, query)
    assert api.get('/auth/google/callback', params={'state': query['state'][0], 'code': 'verified'}).status_code == 200
    assert len(users.documents) == 2
    assert users.documents['alice']['_id'] != users.documents['google:12345']['_id']
    assert api.post('/auth/login', data={'username': 'google:12345', 'password': PASSWORD}).status_code == 401


def test_storage_errors_are_sanitized(client, monkeypatch):
    from pymongo.errors import PyMongoError
    api, users = client
    add_google(users)
    monkeypatch.setattr(users, 'find_one_and_update', AsyncMock(side_effect=PyMongoError('private-provider-details')))
    response = api.put('/auth/credentials', json={'username': 'Traveler', 'password': PASSWORD}, headers=headers())
    assert response.status_code == 503 and 'private-provider-details' not in response.text


@pytest.mark.parametrize('name', ['x', 'Legacy'+'x'*40])
def test_legacy_bcrypt_names_outside_new_signup_format_still_login(client, name):
    from pwdlib.hashers.bcrypt import BcryptHasher
    api, users = client
    users.documents[name] = {'_id': 'legacy-id', 'username': name, 'password': BcryptHasher().hash(PASSWORD)}
    asyncio.run(migrate_login_names(users, apply=True))
    result = api.post('/auth/login', data={'username': name.lower(), 'password': PASSWORD})
    assert result.status_code == 200
    profile = api.get('/auth/me', headers={'Authorization': 'Bearer '+result.json()['access_token']}).json()
    assert profile['username'] == profile['login_username'] == name
