"""JWT signing, validation and revocation checks against simulated users."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from test_auth import client
from virasatguide_backend.data.security import ALGORITHM, create_access_token, get_secret_key


def test_signed_token_claims_and_both_auth_transports(client):
    api, users = client
    users.documents['alice'] = {'_id': 'test-id', 'username': 'alice', 'token_version': 1}
    token = create_access_token({'sub': 'alice', 'version': 1})
    claims = jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])
    assert claims['sub'] == 'alice' and claims['version'] == 1
    assert 1790 <= claims['exp'] - datetime.now(timezone.utc).timestamp() <= 1800
    assert api.get('/auth/me', headers={'Authorization': 'Bearer ' + token}).status_code == 200
    api.cookies.set('virasat_access_token', token, domain='testserver.local', path='/')
    assert api.get('/auth/me').status_code == 200
    assert api.post('/auth/logout', headers={'Origin': 'http://127.0.0.1:5173'}).status_code == 200
    assert 'virasat_access_token' not in api.cookies
    assert api.get('/auth/me', headers={'Authorization': 'Bearer ' + token}).status_code == 401


@pytest.mark.parametrize('kind', ['wrong-key', 'wrong-algorithm', 'unsigned', 'tampered', 'expired', 'missing-sub', 'missing-version', 'unknown-user', 'revoked'])
def test_invalid_jwt_cannot_access_profile(client, kind):
    api, users = client
    users.documents['alice'] = {'_id': 'test-id', 'username': 'alice', 'token_version': 2}
    claims = {'sub': 'alice', 'version': 2, 'exp': datetime.now(timezone.utc) + timedelta(minutes=30)}
    key, algorithm = get_secret_key(), ALGORITHM
    if kind == 'wrong-key':
        key = 'wrong-test-signing-secret-with-32-characters'
    elif kind == 'wrong-algorithm':
        algorithm = 'HS384'
        key = 'wrong-algorithm-test-key-' * 3
    elif kind == 'unsigned':
        algorithm, key = 'none', None
    elif kind == 'expired':
        claims['exp'] = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif kind == 'missing-sub':
        del claims['sub']
    elif kind == 'missing-version':
        del claims['version']
    elif kind == 'unknown-user':
        claims['sub'] = 'missing-user'
    elif kind == 'revoked':
        claims['version'] = 1
    token = jwt.encode(claims, key, algorithm=algorithm)
    if kind == 'tampered':
        head, payload, signature = token.split('.')
        signature = ('A' if signature[0] != 'A' else 'B') + signature[1:]
        token = '.'.join([head, payload, signature])
    assert api.get('/auth/me', headers={'Authorization': 'Bearer ' + token}).status_code == 401
    api.cookies.set('virasat_access_token', token, domain='testserver.local', path='/')
    assert api.get('/auth/me').status_code == 401
