"""Monument listing endpoint tests use an in-memory MongoDB stand-in."""

import copy
import re

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import PyMongoError

from virasatguide_backend.data import database
from virasatguide_backend.main import app


class MemoryCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, field, direction):
        self.documents.sort(key=lambda item: item[field], reverse=direction < 0)
        return self

    def skip(self, count):
        self.documents = self.documents[count:]
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    async def to_list(self):
        return copy.deepcopy(self.documents)


class MemoryMonuments:
    def __init__(self, documents):
        self.documents = documents
        self.last_filter = None

    async def count_documents(self, query):
        if query.get("fail"):
            raise PyMongoError("database unavailable")
        return len(self._matching(query))

    def find(self, query, projection):
        self.last_filter = query
        matches = self._matching(query)
        projected = [
            {field: item[field] for field, included in projection.items() if included and field in item}
            for item in matches
        ]
        return MemoryCursor(projected)

    def _matching(self, query):
        def matches(item, conditions):
            for field, expected in conditions.items():
                if field == "$or":
                    if not any(matches(item, clause) for clause in expected):
                        return False
                elif isinstance(expected, dict) and "$regex" in expected:
                    if not re.search(expected["$regex"], item.get(field, ""), re.I):
                        return False
                elif item.get(field) != expected:
                    return False
            return True
        return [item for item in self.documents if matches(item, query)]


class MemoryDatabase:
    def __init__(self, documents):
        self.monuments = MemoryMonuments(documents)


@pytest.fixture
def api(monkeypatch):
    documents = [
        {"id": 1, "name": "Taj Mahal", "city": "Agra", "location_tag": "Uttar Pradesh, India", "cover_image": "taj.jpg", "reference_text": "private detail"},
        {"id": 2, "name": "Red Fort", "city": "New Delhi", "location_tag": "Delhi, India", "cover_image": "red.jpg", "reference_text": "private detail"},
        {"id": 3, "name": "Qutub Minar", "city": "New Delhi", "location_tag": "Delhi, India", "cover_image": "qutub.jpg", "reference_text": "private detail"},
        {"id": 4, "name": "City Palace", "city": "Jaipur", "location_tag": "Rajasthan, India", "cover_image": "city.jpg", "reference_text": "private detail"},
    ]
    db = MemoryDatabase(documents)
    app.dependency_overrides[database.get_database] = lambda: db
    with TestClient(app) as client:
        yield client, db
    app.dependency_overrides.clear()


def test_list_defaults_and_card_projection(api):
    client, _ = api
    response = client.get("/api/v1/monuments")
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"id": 1, "name": "Taj Mahal", "city": "Agra", "location_tag": "Uttar Pradesh, India", "cover_image": "taj.jpg"},
            {"id": 2, "name": "Red Fort", "city": "New Delhi", "location_tag": "Delhi, India", "cover_image": "red.jpg"},
            {"id": 3, "name": "Qutub Minar", "city": "New Delhi", "location_tag": "Delhi, India", "cover_image": "qutub.jpg"},
            {"id": 4, "name": "City Palace", "city": "Jaipur", "location_tag": "Rajasthan, India", "cover_image": "city.jpg"},
        ],
        "total": 4, "page": 1, "page_size": 12,
    }
    assert "reference_text" not in response.text


@pytest.mark.parametrize(("search", "expected"), [
    ("taj", [1]), ("NEW DELHI", [2, 3]), ("Uttar Pradesh", [1]),
    ("fort", [2]), ("not-found", []),
])
def test_search_matches_name_city_or_location_case_insensitively(api, search, expected):
    client, _ = api
    response = client.get("/api/v1/monuments", params={"search": search})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == expected
    assert response.json()["total"] == len(expected)


def test_city_filter_is_case_insensitive_and_exact(api):
    client, _ = api
    response = client.get("/api/v1/monuments", params={"city": "new delhi"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [2, 3]
    assert client.get("/api/v1/monuments", params={"city": "Delhi"}).json()["total"] == 0


def test_search_and_city_filters_combine(api):
    client, _ = api
    response = client.get("/api/v1/monuments", params={"search": "fort", "city": "New Delhi"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [2]


def test_pagination_is_stable_and_reports_total(api):
    client, _ = api
    first = client.get("/api/v1/monuments", params={"page": 1, "page_size": 2}).json()
    second = client.get("/api/v1/monuments", params={"page": 2, "page_size": 2}).json()
    assert [item["id"] for item in first["items"]] == [1, 2]
    assert [item["id"] for item in second["items"]] == [3, 4]
    assert first["total"] == second["total"] == 4
    assert second["page"] == 2 and second["page_size"] == 2


def test_past_last_page_returns_empty_list(api):
    client, _ = api
    data = client.get("/api/v1/monuments", params={"page": 4, "page_size": 2}).json()
    assert data["items"] == []
    assert data["total"] == 4


@pytest.mark.parametrize(("params", "expected_status"), [
    ({"page": 0}, 422), ({"page": -1}, 422), ({"page_size": 0}, 422),
    ({"page_size": 51}, 422), ({"search": "x" * 101}, 422), ({"city": "x" * 101}, 422),
])
def test_query_parameter_validation(api, params, expected_status):
    client, _ = api
    assert client.get("/api/v1/monuments", params=params).status_code == expected_status


def test_database_error_returns_service_unavailable(monkeypatch):
    db = MemoryDatabase([])
    async def fail(_query):
        raise PyMongoError("database unavailable")
    db.monuments.count_documents = fail
    app.dependency_overrides[database.get_database] = lambda: db
    try:
        response = TestClient(app).get("/api/v1/monuments")
        assert response.status_code == 503
        assert response.json() == {"detail": "Monument catalogue is unavailable"}
    finally:
        app.dependency_overrides.clear()
