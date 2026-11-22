"""Frontend-compatible autocomplete, bounded official requests and safe output."""

import httpx
import pytest
from fastapi.testclient import TestClient

from virasatguide_backend.main import app
from virasatguide_backend.travel import places


@pytest.fixture
def api(monkeypatch):
    original = httpx.AsyncClient
    payload = {'results': [{'id': 1269515, 'name': 'Jaipur', 'admin1': 'Rajasthan', 'country': 'India',
                           'country_code': 'IN', 'latitude': 26.91962, 'longitude': 75.78781}]}
    requests = []
    def handle(request):
        requests.append(request)
        assert request.url.host == 'geocoding-api.open-meteo.com'
        assert request.url.params['count'] == '6'
        return httpx.Response(200, json=payload)
    monkeypatch.setattr(places.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    yield TestClient(app), payload, requests


def test_suggestions_are_public_structured_and_deduplicated(api):
    client, payload, calls = api
    payload['results'] *= 2
    response = client.get('/api/v1/travel/places/suggest?q=jaip')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert len(response.json()) == 1
    item = response.json()[0]
    assert item['label'] == 'Jaipur, Rajasthan, India'
    assert item['country_code'] == 'IN' and item['id'] == 'geonames:1269515'
    assert item['latitude'] == 26.91962 and item['provider'] == 'Open-Meteo / GeoNames'
    assert calls[0].url.params['name'] == 'jaip'


@pytest.mark.parametrize('query', ['', 'x'*121, '  ', '<script>'])
def test_suggestion_input_validation(api, query):
    assert api[0].get('/api/v1/travel/places/suggest', params={'q': query}).status_code == 422


def test_short_queries_and_no_matches(api):
    client, payload, calls = api
    assert client.get('/api/v1/travel/places/suggest?q=j').json() == []
    assert not calls
    payload.clear()
    assert client.get('/api/v1/travel/places/suggest?q=unknown').json() == []


@pytest.mark.parametrize('change', [{'name': '<script>alert(1)</script>'}, {'country': 'Bad\nCountry'},
                                    {'latitude': 100}, {'longitude': float('inf')}, {'country_code': 'BAD'}])
def test_invalid_places_are_not_returned(api, change):
    client, payload, _ = api
    payload['results'][0].update(change)
    # JSON itself does not permit infinity, so represent a corrupt numeric string.
    if change.get('longitude') == float('inf'):
        payload['results'][0]['longitude'] = 'Infinity'
    response = client.get('/api/v1/travel/places/suggest?q=jaip')
    assert response.status_code == 502
    assert '<script>' not in response.text


@pytest.mark.parametrize('mode,status', [('timeout', 504), ('offline', 503), ('http-error', 503), ('bad-json', 502), ('bad-shape', 502)])
def test_location_provider_errors_are_clear_and_private(monkeypatch, mode, status):
    original = httpx.AsyncClient
    def handle(request):
        if mode == 'timeout':
            raise httpx.ReadTimeout('private-provider-error')
        if mode == 'offline':
            raise httpx.ConnectError('private-provider-error')
        if mode == 'bad-json':
            return httpx.Response(200, text='private-provider-error')
        return httpx.Response(500 if mode == 'http-error' else 200, json={'results': 'private-provider-error'})
    monkeypatch.setattr(places.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    response = TestClient(app).get('/api/v1/travel/places/suggest?q=jaip')
    assert response.status_code == status
    assert 'private-provider-error' not in response.text and 'manually' in response.text
