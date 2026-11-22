"""Travel workflows use simulated storage and model calls, with real PDF rendering."""

import asyncio
import copy
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pymongo.errors import PyMongoError
from pypdf import PdfReader

from virasatguide_ai_agent import StoryResult
from virasatguide_ai_agent import providers
from virasatguide_backend.data.database import get_database, get_users_collection
from virasatguide_backend.main import app
from virasatguide_backend.routes.auth import get_current_user
from virasatguide_backend.travel import llm, planner, retrieval, router
from virasatguide_backend.travel.pdf import render_plan_pdf
from virasatguide_backend.travel.schemas import AnswerDraft, Evidence, PlanDraft, PlanInput, QuestionInput, TripPlan

MONUMENT = {"id": 1, "name": "Taj Mahal", "city": "Agra", "reference_text": "Shah Jahan commissioned the Taj Mahal in 1632."}
STORY = StoryResult(monument_id=1, story="Shah Jahan commissioned this marble monument in Agra.", sources=[], research_status="database_only")


def draft(days=1):
    return PlanDraft(title="Agra in a day", summary="A relaxed heritage visit.", days=[
        {"day": i, "title": "Marble and gardens", "stops": [{
            "place_name": "Taj Mahal", "area": "Taj Ganj", "time_of_day": "morning",
            "description": "Explore the monument and surrounding gardens.", "duration_minutes": 120,
            "estimated_cost": 500, "evidence_ids": ["monument-1"],
        }], "transport_cost": 200, "accommodation_cost": 0} for i in range(1, days+1)
    ], tips=["Carry drinking water."])


class Collection:
    def __init__(self):
        self.docs = {}
        self.force_conflict = False

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = copy.deepcopy(doc)

    async def find_one(self, query):
        doc = self.docs.get(query["_id"])
        return copy.deepcopy(doc) if doc and doc["owner"] == query["owner"] else None

    async def update_one(self, query, update):
        doc = self.docs[query["_id"]]
        if self.force_conflict or doc["version"] != query["version"]:
            return SimpleNamespace(modified_count=0)
        doc["view"]["history"] += copy.deepcopy(update["$push"]["view.history"]["$each"])
        doc["view"]["history"] = doc["view"]["history"][update["$push"]["view.history"]["$slice"]:]
        doc["version"] += 1
        return SimpleNamespace(modified_count=1)


class Monuments:
    def find(self, query, fields):
        self.query = query
        return self
    def sort(self, *args): return self
    def limit(self, *args): return self
    async def to_list(self): return [MONUMENT]


@pytest.fixture
def api(monkeypatch):
    db = SimpleNamespace(tour_sessions=Collection(), trip_plans=Collection(), monuments=Monuments())
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_users_collection] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: {"username": "alice"}
    monkeypatch.setattr(router, "get_or_create_story", AsyncMock(return_value=STORY))
    monkeypatch.setattr(router, "get_monument", AsyncMock(return_value=MONUMENT))
    monkeypatch.setattr(planner, "search_destination", lambda _: [])
    monkeypatch.setattr(planner, "generate_json", AsyncMock(return_value=draft()))
    monkeypatch.setattr(retrieval, "generate_json", AsyncMock(return_value=AnswerDraft(
        answer="Shah Jahan commissioned it.", evidence_ids=["reference-1"], supported=True)))
    yield TestClient(app), db
    app.dependency_overrides.clear()


def test_travel_auth_and_invalid_inputs(api):
    client, _ = api
    app.dependency_overrides.pop(get_current_user)
    assert client.post("/api/v1/travel/plans", json={"destination": "Agra"}).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: {"username": "alice"}
    for body in [{"destination": " "}, {"destination": "Agra", "days": 0},
                 {"destination": "Agra", "budget": -1}, {"destination": "Agra", "location": {"latitude": 99, "longitude": 0}}]:
        assert client.post("/api/v1/travel/plans", json=body).status_code == 422
    assert client.get("/api/v1/travel/plans/invalid/pdf").status_code == 422
    assert client.post("/api/v1/travel/tour-sessions", json={"monument_id": 0}).status_code == 422


def test_session_retrieval_chat_retry_and_ownership(api):
    client, db = api
    response = client.post("/api/v1/travel/tour-sessions", json={"monument_id": 1})
    assert response.status_code == 201
    session = response.json()["session_id"]
    path = f"/api/v1/travel/tour-sessions/{session}"
    key = str(uuid4())
    question = {"question": "Who commissioned it?", "request_id": key}
    response = client.post(path + "/questions", json=question)
    assert response.status_code == 200
    assert response.json()["evidence"][0]["kind"] == "monument_reference"
    assert client.post(path + "/questions", json=question).json() == response.json()
    assert len(client.get(path).json()["history"]) == 1
    assert client.post(path + "/questions", json={**question, "question": "When?"}).status_code == 409
    assert client.post(path + "/questions", json={"question": "   "}).status_code == 422
    db.tour_sessions.force_conflict = True
    assert client.post(path + "/questions", json={"question": "What year?"}).status_code == 409
    app.dependency_overrides[get_current_user] = lambda: {"username": "bob"}
    assert client.get(path).status_code == 404
    assert client.post(path + "/questions", json={"question": "Who?"}).status_code == 404


def test_rag_uses_facts_and_declines_unsupported(monkeypatch):
    chunks = retrieval.story_evidence(MONUMENT, STORY.model_dump(mode="json"))
    assert retrieval.retrieve("1632", chunks)[0].id == "reference-1"
    call = AsyncMock(return_value=AnswerDraft(answer="An unsupported guess", supported=False, evidence_ids=[]))
    monkeypatch.setattr(retrieval, "generate_json", call)
    text, supported, evidence = asyncio.run(retrieval.answer_question("What is the weather?", chunks, []))
    assert not supported and evidence == [] and "enough information" in text
    call.return_value = AnswerDraft(answer="A claim", supported=True, evidence_ids=["invented"])
    with pytest.raises(HTTPException) as error:
        asyncio.run(retrieval.answer_question("Who?", chunks, []))
    assert error.value.status_code == 502


def test_plan_budget_saved_pdf_and_ownership(api):
    client, _ = api
    response = client.post("/api/v1/travel/plans", json={"destination": "Agra", "area": "Taj Ganj", "budget": 600})
    assert response.status_code == 201, response.text
    plan = response.json()
    assert isinstance(planner.generate_json.call_args.args[1], str)
    assert plan["estimated_total"] == 700
    assert plan["days"][0]["estimated_total"] == 700
    assert plan["budget_status"] == "over_budget"
    assert plan["proximity_status"] == "area_search"
    path = f"/api/v1/travel/plans/{plan['plan_id']}"
    assert client.get(path).json() == plan
    pdf = client.get(path + "/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    assert "attachment" in pdf.headers["content-disposition"]
    text = " ".join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages)
    assert "Taj Mahal" in text and "INR 700" in text and "Sources & references" in text
    app.dependency_overrides[get_current_user] = lambda: {"username": "bob"}
    assert client.get(path).status_code == 404
    assert client.get(path + "/pdf").status_code == 404


@pytest.mark.parametrize("budget,status", [(None, "not_set"), (700, "within_budget"), (0, "over_budget")])
def test_budget_modes(api, budget, status):
    client, _ = api
    response = client.post("/api/v1/travel/plans", json={"destination": "Agra", "budget": budget,
        "location": {"latitude": 27.1751, "longitude": 78.0421}})
    assert response.json()["budget_status"] == status
    assert response.json()["proximity_status"] == "coordinates_unverified"


def test_plan_rejects_wrong_days_unknown_sources_and_bad_schedule(api, monkeypatch):
    client, _ = api
    assert client.post("/api/v1/travel/plans", json={"destination": "Agra", "days": 2}).status_code == 502
    model = draft()
    model.days[0].stops[0].evidence_ids = ["fake"]
    monkeypatch.setattr(planner, "generate_json", AsyncMock(return_value=model))
    assert client.post("/api/v1/travel/plans", json={"destination": "Agra"}).status_code == 502
    model = draft()
    second = model.days[0].stops[0].model_copy(update={"time_of_day": "evening", "duration_minutes": 480})
    model.days[0].stops = [second, model.days[0].stops[0]]
    monkeypatch.setattr(planner, "generate_json", AsyncMock(return_value=model))
    assert client.post("/api/v1/travel/plans", json={"destination": "Agra"}).status_code == 502


def test_storage_failure_has_clear_response(api, monkeypatch):
    client, db = api
    monkeypatch.setattr(db.trip_plans, "find_one", AsyncMock(side_effect=PyMongoError("offline")))
    assert client.get(f"/api/v1/travel/plans/{uuid4()}").status_code == 503


@pytest.mark.parametrize("mode,code", [("invalid", 502), ("missing", 503), ("timeout", 504), ("offline", 503)])
def test_ollama_error_mapping(monkeypatch, mode, code):
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    original = httpx.AsyncClient
    def handle(request):
        if mode == "timeout": raise httpx.ReadTimeout("slow")
        if mode == "offline": raise httpx.ConnectError("offline")
        return httpx.Response(404 if mode == "missing" else 200, json={"message": {"content": "{}"}})
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kw: original(transport=transport, **kw))
    with pytest.raises(HTTPException) as error:
        asyncio.run(llm.generate_json(AnswerDraft, "Answer", {}))
    assert error.value.status_code == code


def test_pdf_handles_long_content_and_markup():
    data = draft(7)
    for day in data.days:
        day.stops[0].description = "Historic gardens <with> fountains & paths. " * 20
    plan = TripPlan(plan_id=uuid4(), created_at=datetime.now(timezone.utc), request=PlanInput(destination="Agra", days=7),
                    title=data.title, summary=data.summary,
                    days=[{**d.model_dump(), "estimated_total": 700} for d in data.days],
                    estimated_total=4900, budget_status="not_set", sources=[], tips=data.tips, warnings=[],
                    proximity_status="destination_search")
    pdf = render_plan_pdf(plan)
    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) > 2
    text = " ".join(p.extract_text() for p in reader.pages)
    assert "Day 7" in text and "<with>" in text


def test_empty_catalogue_and_failed_search_returns_service_error(api, monkeypatch):
    client, db = api
    monkeypatch.setattr(db.monuments, "to_list", AsyncMock(return_value=[]))
    response = client.post("/api/v1/travel/plans", json={"destination": "Unknown place"})
    assert response.status_code == 503


def test_followup_history_is_capped(api):
    client, _ = api
    session = client.post("/api/v1/travel/tour-sessions", json={"monument_id": 1}).json()["session_id"]
    path = f"/api/v1/travel/tour-sessions/{session}"
    for i in range(12):
        assert client.post(path + "/questions", json={"question": f"Question {i}"}).status_code == 200
    history = client.get(path).json()["history"]
    assert [pair["question"] for pair in history] == ["Question 9", "Question 10", "Question 11"]
    # The database itself retains only these pairs, not just the API response.
    saved = next(iter(api[1].tour_sessions.docs.values()))
    assert [pair["question"] for pair in saved["view"]["history"]] == ["Question 9", "Question 10", "Question 11"]


def test_web_search_fallback_uses_area_coordinates_and_rejects_unsafe_urls(monkeypatch):
    from ddgs.exceptions import DDGSException
    queries = []
    class Search:
        def __init__(self, **kwargs): pass
        def text(self, query, **kwargs):
            queries.append((query, kwargs["backend"]))
            if kwargs["backend"] == "duckduckgo": raise DDGSException("failed")
            return [{"title": "Official tourism", "href": "https://example.org/agra", "body": "Visit the Taj Mahal in Agra."},
                    {"title": "Unsafe", "href": "http://example.org", "body": "text"}]
    monkeypatch.setattr(planner, "DDGS", Search)
    request = PlanInput(destination="Taj Mahal", city="Agra", area="Taj Ganj", location={"latitude": 27, "longitude": 78})
    sources = planner.search_destination(request)
    assert [q[1] for q in queries] == ["duckduckgo", "bing"]
    assert "Taj Ganj Agra" in queries[0][0] and "27.0,78.0" in queries[0][0]
    assert len(sources) == 1 and sources[0].url == "https://example.org/agra"


def test_literal_escapes_and_html_are_plain_text():
    assert QuestionInput(question=r"Who\n built\t it?").question == "Who built it?"
    with pytest.raises(ValidationError):
        QuestionInput(question=r"\n\t")


def test_ollama_accepts_validated_json(monkeypatch):
    monkeypatch.setenv("OLLAMA_THINK", "false")
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    original = httpx.AsyncClient
    def handle(request):
        import json
        payload = json.loads(request.content)
        assert payload["format"]["properties"]["answer"]["type"] == "string"
        assert payload["stream"] is False
        assert payload["think"] is False
        return httpx.Response(200, json={"message": {"content": '{"answer":"Known fact", "evidence_ids":["reference-1"], "supported":true}'}})
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    assert asyncio.run(llm.generate_json(AnswerDraft, "Answer", {})).answer == "Known fact"


def test_plan_rejects_invented_venue_and_repairs_once(api, monkeypatch):
    client, _ = api
    invalid = draft()
    invalid.days[0].stops[0].place_name = "Invented Palace Restaurant"
    generate = AsyncMock(side_effect=[invalid, draft()])
    monkeypatch.setattr(planner, "generate_json", generate)
    response = client.post("/api/v1/travel/plans", json={"destination": "Agra"})
    assert response.status_code == 201
    assert generate.await_count == 2
    assert "validation_feedback" in generate.call_args.args[2]
    assert generate.call_args.args[2]["previous_draft"]["days"][0]["stops"][0]["place_name"] == "Invented Palace Restaurant"


@pytest.mark.parametrize("fallback", [False, True])
def test_plan_endpoint_uses_shared_primary_and_fallback_provider(api, monkeypatch, fallback):
    import json
    client, _ = api
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(planner, "generate_json", llm.generate_json)
    original = httpx.AsyncClient
    seen = []
    def handle(request):
        seen.append(request.url.host)
        if request.url.host == "generativelanguage.googleapis.com":
            body = json.loads(request.content)
            assert body["generationConfig"]["responseJsonSchema"] == PlanDraft.model_json_schema()
            if fallback: return httpx.Response(429)
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": draft().model_dump_json()}]}}]})
        return httpx.Response(200, json={"message": {"content": draft().model_dump_json()}})
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    response = client.post("/api/v1/travel/plans", json={"destination": "Agra", "budget": 1000})
    assert response.status_code == 201, response.text
    assert response.json()["estimated_total"] == 700
    assert response.json()["budget_status"] == "within_budget"
    plan_id = response.json()["plan_id"]
    assert client.get(f"/api/v1/travel/plans/{plan_id}/pdf").headers["content-type"] == "application/pdf"
    assert seen == ["generativelanguage.googleapis.com"] + (["127.0.0.1"] if fallback else [])


def test_question_endpoint_uses_gemini_with_retrieved_evidence(api, monkeypatch):
    import json
    client, _ = api
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(retrieval, "generate_json", llm.generate_json)
    original = httpx.AsyncClient
    def handle(request):
        context = json.loads(json.loads(request.content)["contents"][0]["parts"][0]["text"])
        assert "Shah Jahan" in str(context)
        result = {"answer": "Shah Jahan commissioned it.", "evidence_ids": ["reference-1"], "supported": True}
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(result)}]}}]})
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    session_id = client.post("/api/v1/travel/tour-sessions", json={"monument_id": 1}).json()["session_id"]
    response = client.post(f"/api/v1/travel/tour-sessions/{session_id}/questions", json={"question": "Who commissioned it?"})
    assert response.status_code == 200, response.text
    assert response.json()["supported"] is True
    assert response.json()["evidence"][0]["id"] == "reference-1"


@pytest.mark.parametrize("interests", [[""], ["   "], ["a" * 81], ["history"] * 9])
def test_plan_rejects_invalid_interests(interests):
    with pytest.raises(ValidationError):
        PlanInput(destination="Agra", interests=interests)


def test_plan_trims_interest_names():
    assert PlanInput(destination="Agra", interests=[" heritage "]).interests == ["heritage"]


def test_retrieval_prefers_matching_passages_and_keeps_four():
    chunks = [Evidence(id=str(i), title="Tour notes", text=text, kind="monument_reference")
              for i, text in enumerate(["red sandstone walls", "marble", "marble gardens", "river bank", "local food"])]
    selected = retrieval.retrieve("marble gardens", chunks)
    assert selected[0].text == "marble gardens"
    assert len(selected) == 4
