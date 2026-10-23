"""Exercise the OAuth flow with signed test tokens and no external services."""
import json
import time
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from test_auth import client
from virasatguide_backend.main import app
from virasatguide_backend.routes.google_oauth import get_google_client


@pytest.fixture
def google_flow(client, monkeypatch):
    api, users = client
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'test-client')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'test-secret')
    monkeypatch.setenv('GOOGLE_REDIRECT_URI', 'http://localhost/auth/google/callback')
    google = get_google_client()
    google.load_server_metadata = AsyncMock(return_value={
        'authorization_endpoint': 'https://accounts.google.com/o/oauth2/v2/auth',
        'token_endpoint': 'https://oauth2.googleapis.com/token',
        'issuer': 'https://accounts.google.com',
        'id_token_signing_alg_values_supported': ['RS256'],
    })
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid='test-key', use='sig', alg='RS256')
    google.fetch_jwk_set = AsyncMock(return_value={'keys': [jwk]})
    google.fetch_access_token = AsyncMock()
    app.dependency_overrides[get_google_client] = lambda: google
    yield api, users, google, key


def start(api):
    response = api.get('/auth/google/login', follow_redirects=False)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers['location']).query)
    assert query['code_challenge_method'] == ['S256']
    assert 'httponly' in response.headers['set-cookie'].lower()
    return query


def set_token(google, key, query, **overrides):
    claims = dict(iss='https://accounts.google.com', aud='test-client', sub='12345',
                  exp=int(time.time()) + 300, iat=int(time.time()),
                  nonce=query['nonce'][0], email='alice@example.com', email_verified=True)
    claims.update(overrides)
    google.fetch_access_token.return_value = {
        'id_token': jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'test-key'})
    }


def test_google_login_profile_repeat_and_logout(google_flow):
    api, users, google, key = google_flow
    for _ in range(2):
        query = start(api)
        set_token(google, key, query)
        response = api.get('/auth/google/callback', params={'state': query['state'][0], 'code': 'test-code'})
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'no-store'
        headers = {'Authorization': 'Bearer ' + response.json()['access_token']}
        assert api.get('/auth/me', headers=headers).json()['username'] == 'google:12345'
        assert api.post('/auth/logout', headers=headers).status_code == 200
        assert api.get('/auth/me', headers=headers).status_code == 401
    assert len(users.documents) == 1
    assert 'password' not in users.documents['google:12345']
    google.fetch_access_token.assert_awaited()
    assert google.fetch_access_token.call_args.kwargs['code_verifier']


@pytest.mark.parametrize('overrides', [
    {'nonce': 'wrong'}, {'aud': 'other-client'}, {'iss': 'https://attacker.example'},
    {'exp': int(time.time()) - 600}, {'email_verified': False},
])
def test_invalid_identity_is_rejected(google_flow, overrides):
    api, users, google, key = google_flow
    query = start(api)
    set_token(google, key, query, **overrides)
    response = api.get('/auth/google/callback', params={'state': query['state'][0], 'code': 'test-code'})
    assert response.status_code in (400, 401)
    assert not users.documents


def test_wrong_state_and_denial(google_flow):
    api, users, google, key = google_flow
    start(api)
    assert api.get('/auth/google/callback?code=test&state=wrong').status_code == 400
    google.fetch_access_token.assert_not_awaited()
    assert api.get('/auth/google/callback?error=access_denied').status_code == 400
    assert not users.documents


def test_google_configuration_and_reserved_username(client, monkeypatch):
    api, _ = client
    monkeypatch.delenv('GOOGLE_CLIENT_ID', raising=False)
    assert api.get('/auth/google/login').status_code == 503
    assert api.post('/auth/register', json={'username': 'google:12345', 'password': 'test-password'}).status_code == 400


def test_invalid_signature_is_rejected(google_flow):
    api, users, google, _ = google_flow
    query = start(api)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    set_token(google, other_key, query)
    assert api.get('/auth/google/callback', params={'state': query['state'][0], 'code': 'test'}).status_code == 400
    assert not users.documents


def test_existing_username_is_not_taken_over(google_flow):
    api, users, google, key = google_flow
    users.documents['google:12345'] = {'username': 'google:12345', 'password': 'existing-hash'}
    query = start(api)
    set_token(google, key, query)
    assert api.get('/auth/google/callback', params={'state': query['state'][0], 'code': 'test'}).status_code == 409
    assert users.documents['google:12345']['password'] == 'existing-hash'



def test_browser_cookie_redirect_profile_and_logout(google_flow, monkeypatch):
    api, users, google, key = google_flow
    destination = 'http://127.0.0.1:5173/src/dashboard/index.html'
    monkeypatch.setenv('FRONTEND_URL', destination)
    query = start(api)
    set_token(google, key, query)
    response = api.get('/auth/google/callback', params={'state': query['state'][0], 'code': 'test'}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == destination
    assert 'access_token' not in response.headers['location']
    cookies = response.headers.get_list('set-cookie')
    auth_cookie = next(c for c in cookies if c.startswith('virasat_access_token='))
    assert 'HttpOnly' in auth_cookie and 'SameSite=lax' in auth_cookie and 'Max-Age=1800' in auth_cookie
    profile = api.get('/auth/me')
    assert profile.status_code == 200
    assert profile.json()['email'] == 'alice@example.com'
    assert 'password' not in profile.json()
    assert api.post('/auth/logout', headers={'Origin': 'https://attacker.example'}).status_code == 403
    assert api.post('/auth/logout').status_code == 403
    response = api.post('/auth/logout', headers={'Origin': 'http://127.0.0.1:5173'})
    assert response.status_code == 200
    assert 'virasat_access_token' not in api.cookies
    assert api.get('/auth/me').status_code == 401


def test_cors_allows_only_configured_frontend(client):
    api, _ = client
    response = api.options('/auth/logout', headers={
        'Origin': 'http://127.0.0.1:5173', 'Access-Control-Request-Method': 'POST',
    })
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == 'http://127.0.0.1:5173'
    assert response.headers['access-control-allow-credentials'] == 'true'
    response = api.options('/auth/logout', headers={
        'Origin': 'https://attacker.example', 'Access-Control-Request-Method': 'POST',
    })
    assert response.status_code == 400
    assert 'access-control-allow-origin' not in response.headers


def test_frontend_url_rejects_untrusted_schemes(monkeypatch):
    from virasatguide_backend.data.security import get_frontend_url
    for value in ['javascript:alert(1)', '//attacker.example', 'http://example.com/dashboard', 'https://user:pass@example.com']:
        monkeypatch.setenv('FRONTEND_URL', value)
        with pytest.raises(RuntimeError):
            get_frontend_url()



def test_readiness_without_credentials_returns_no_secrets(client, monkeypatch):
    api, _ = client
    monkeypatch.delenv('GOOGLE_CLIENT_ID', raising=False)
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'must-never-appear')
    response = api.get('/auth/google/status')
    assert response.status_code == 200
    assert response.json()['configured'] is False
    assert 'must-never-appear' not in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_readiness_with_credentials(client, monkeypatch):
    api, _ = client
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'example.apps.googleusercontent.com')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'must-never-appear')
    monkeypatch.setenv('GOOGLE_REDIRECT_URI', 'http://127.0.0.1:8000/auth/google/callback')
    response = api.get('/auth/google/status')
    assert response.json() == {'configured': True}
    assert 'must-never-appear' not in response.text


@pytest.mark.parametrize('params,code', [
    ({'error': 'access_denied'}, 'cancelled'),
    ({'code': 'invalid', 'state': 'forged'}, 'invalid'),
])
def test_failed_browser_signin_returns_to_frontend(google_flow, monkeypatch, params, code):
    api, _, google, _ = google_flow
    monkeypatch.setenv('FRONTEND_URL', 'http://127.0.0.1:5173/src/dashboard/index.html')
    start(api)
    response = api.get('/auth/google/callback', params=params, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == 'http://127.0.0.1:5173/src/dashboard/index.html?auth_error=' + code
    assert 'google_oauth' not in api.cookies
    google.fetch_access_token.assert_not_awaited()
