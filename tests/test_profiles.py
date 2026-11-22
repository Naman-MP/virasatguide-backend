"""Profile ownership, validation and public projection; no external accounts."""

from datetime import datetime, timedelta, timezone
import pytest
from test_auth import client
from virasatguide_backend.data.security import create_access_token


def login_headers():
    """Build the bearer headers used by the profile tests."""
    return {"Authorization": "Bearer " + create_access_token({"sub": "alice", "version": 1})}


@pytest.fixture
def profile_api(client):
    """Provide the FastAPI test client and isolated state for this module."""
    api, users = client
    users.documents["alice"] = {"_id": "alice-id", "username": "alice", "token_version": 1,
                                "password": "private-hash", "private_key": "must-not-leak"}
    users.documents["bob"] = {"_id": "bob-id", "username": "bob", "token_version": 1}
    return api, users


def test_profile_update_is_owned_private_and_persistent(profile_api):
    """Verify profile update is owned private and persistent."""
    api, users = profile_api
    body = {"full_name": "Submitted Traveler", "date_of_birth": "2003-08-12", "gender": "male"}
    assert api.put('/auth/profile', json=body).status_code == 401
    response = api.put('/auth/profile', json=body, headers=login_headers())
    assert response.status_code == 200
    assert response.json()['full_name'] == 'Submitted Traveler'
    assert response.json()['onboarding_completed'] is True
    assert response.json()['date_of_birth'] == '2003-08-12'
    assert response.headers['cache-control'] == 'private, no-store'
    assert 'private-hash' not in response.text and 'must-not-leak' not in response.text
    assert users.documents['alice']['date_of_birth'] == '2003-08-12'
    assert 'full_name' not in users.documents['bob']
    assert api.get('/auth/me', headers=login_headers()).json() == response.json()
    assert api.put('/auth/profile', json={**body, 'username': 'bob'}, headers=login_headers()).status_code == 422


@pytest.mark.parametrize('gender', ['male','female','non_binary','other','prefer_not_to_say',None])
def test_profile_gender_disclosure_is_optional(profile_api, gender):
    """Verify profile gender disclosure is optional."""
    api, _ = profile_api
    response = api.put('/auth/profile', json={'full_name':'Traveler','date_of_birth':'2000-02-29','gender':gender}, headers=login_headers())
    assert response.status_code == 200
    assert response.json()['gender'] == (gender or 'prefer_not_to_say')


@pytest.mark.parametrize('change', [
    {'full_name':' '}, {'gender':'unknown'}, {'date_of_birth':'2001-02-29'},
    {'date_of_birth':'1800-01-01'}, {'date_of_birth':'2000-1-1'}, {'date_of_birth':12345},
    {'date_of_birth':(datetime.now(timezone.utc)+timedelta(days=1)).date().isoformat()},
])
def test_profile_rejects_invalid_values(profile_api, change):
    """Verify profile rejects invalid values."""
    api, _ = profile_api
    body={'full_name':'Traveler','date_of_birth':'2000-01-01',**change}
    assert api.put('/auth/profile',json=body,headers=login_headers()).status_code == 422


def test_profile_cookie_writes_require_trusted_origin(profile_api):
    """Verify profile cookie writes require trusted origin."""
    api,_=profile_api
    api.cookies.set('virasat_access_token',login_headers()['Authorization'][7:],domain='testserver.local',path='/')
    body={'full_name':'Traveler','date_of_birth':'2000-01-01'}
    assert api.put('/auth/profile',json=body).status_code == 403
    assert api.put('/auth/profile',json=body,headers={'Origin':'https://attacker.example'}).status_code == 403
    assert api.put('/auth/profile',json=body,headers={'Origin':'http://127.0.0.1:5173'}).status_code == 200
    preflight=api.options('/auth/profile',headers={'Origin':'https://virasat-frontend-nu.vercel.app','Access-Control-Request-Method':'PUT','Access-Control-Request-Headers':'Content-Type'})
    assert preflight.status_code == 200 and preflight.headers['access-control-allow-credentials']=='true'


def test_birth_date_is_optional_can_be_kept_or_cleared(profile_api):
    """Verify birth date is optional can be kept or cleared."""
    api, users = profile_api
    response = api.put('/auth/profile', json={'full_name': 'Traveler'}, headers=login_headers())
    assert response.status_code == 200
    assert response.json()['date_of_birth'] is None and response.json()['onboarding_completed'] is True
    assert response.json()['credentials_set'] is True
    api.put('/auth/profile', json={'full_name': 'Traveler', 'date_of_birth': '2000-01-01'}, headers=login_headers())
    response = api.put('/auth/profile', json={'full_name': 'Updated'}, headers=login_headers())
    assert response.json()['date_of_birth'] == '2000-01-01'
    response = api.put('/auth/profile', json={'full_name': 'Updated', 'date_of_birth': None}, headers=login_headers())
    assert response.json()['date_of_birth'] is None
    assert users.documents['alice']['onboarding_completed'] is True
