"""Authenticated travel endpoints. Every saved resource belongs to its creator."""

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from pymongo.errors import PyMongoError

from virasatguide_backend.data.database import get_database
from virasatguide_backend.data.monuments import get_monument
from virasatguide_backend.routes.auth import get_current_user
from virasatguide_backend.services.stories import get_or_create_story
from .capabilities import Capabilities, get_capabilities
from .search import search_transport, search_hotels
from .search_models import future_dates, TransportSearchInput, TransportSearchResult, HotelSearchInput, HotelSearchResult
from .places import PlaceSuggestion, suggest_places
from .pdf import render_plan_pdf
from .planner import create_plan
from .retrieval import answer_question, story_evidence
from .schemas import CHAT_HISTORY_LIMIT, Answer, Evidence, PlanInput, QuestionInput, SessionInput, TourSession, TripPlan

router = APIRouter(prefix="/api/v1/travel", tags=["Travel"], responses={
    401: {"description": "Sign in required."},
    404: {"description": "Resource not found for this user."},
    409: {"description": "Another question is being saved or a request ID was reused."},
    502: {"description": "The model returned invalid output."},
    503: {"description": "Database, destination research or configured AI providers are unavailable."},
    504: {"description": "The final AI provider call timed out."},
})


async def storage(database=Depends(get_database)):
    """Translate MongoDB failures into the travel API's storage error response."""
    try:
        yield database
    except PyMongoError as exc:
        raise HTTPException(503, "Travel storage is unavailable") from exc


Database = Annotated[object, Depends(storage)]
User = Annotated[dict, Depends(get_current_user)]


async def get_owned_document(collection, resource_id: UUID, user: dict):
    """Read a private resource only when its owner matches the current user."""
    document = await collection.find_one({"_id": str(resource_id), "owner": user["username"]})
    if document is None:
        raise HTTPException(404, "Resource not found")
    return document


@router.post("/tour-sessions", response_model=TourSession, status_code=201)
async def start_tour(request: SessionInput, database: Database, user: User):
    """Create a private Q&A session using the selected monument and its story."""
    monument = await get_monument(database, request.monument_id)
    if monument is None:
        raise HTTPException(404, "Monument not found")
    story = await get_or_create_story(database, request.monument_id, monument=monument)
    view = TourSession(session_id=uuid4(), monument_id=request.monument_id,
                       monument_name=monument["name"], story=story.story,
                       created_at=datetime.now(timezone.utc), history=[])
    evidence = story_evidence(monument, story.model_dump(mode="json"))
    await database.tour_sessions.insert_one({
        "_id": str(view.session_id), "owner": user["username"], "version": 0,
        "view": view.model_dump(mode="json"), "evidence": [e.model_dump() for e in evidence],
    })
    return view


@router.get("/tour-sessions/{session_id}", response_model=TourSession)
async def read_tour(session_id: UUID, database: Database, user: User):
    """Return the user's saved tour with at most the latest three exchanges."""
    saved = await get_owned_document(database.tour_sessions, session_id, user)
    saved["view"]["history"] = saved["view"]["history"][-CHAT_HISTORY_LIMIT:]
    return TourSession.model_validate(saved["view"])


@router.post("/tour-sessions/{session_id}/questions", response_model=Answer)
async def ask_question(session_id: UUID, request: QuestionInput, database: Database, user: User):
    """Answer from saved context, reuse matching retries, and save with a version check."""
    saved = await get_owned_document(database.tour_sessions, session_id, user)
    request_id = request.request_id or uuid4()
    history = saved["view"]["history"][-CHAT_HISTORY_LIMIT:]
    for previous in history:
        if previous["request_id"] == str(request_id):
            if previous["question"] != request.question:
                raise HTTPException(409, "Use a new request_id for a different question")
            return Answer.model_validate(previous)
    text, supported, evidence = await answer_question(
        request.question, [Evidence.model_validate(e) for e in saved["evidence"]], history,
    )
    answer = Answer(request_id=request_id, question=request.question, answer=text,
                    supported=supported, evidence=evidence, created_at=datetime.now(timezone.utc))
    # Only save if another request has not changed this session's version.
    session_query = {"_id": str(session_id), "owner": user["username"], "version": saved["version"]}
    history_update = {
        "$push": {
            "view.history": {
                "$each": [answer.model_dump(mode="json")],
                "$slice": -CHAT_HISTORY_LIMIT,  # Keep the latest three exchanges.
            },
        },
        "$inc": {"version": 1},
    }
    update = await database.tour_sessions.update_one(session_query, history_update)
    if update.modified_count != 1:
        raise HTTPException(409, "Another question changed this session; retry with the same request_id")
    return answer


@router.post("/plans", response_model=TripPlan, status_code=201)
async def plan_trip(request: PlanInput, database: Database, user: User):
    """Research a destination and save itinerary cards with budget estimates."""
    if request.departure_date:
        try:
            future_dates(request.departure_date, request.return_date)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    plan = await create_plan(request, database, owner=user["username"])
    await database.trip_plans.insert_one({"_id": str(plan.plan_id), "owner": user["username"],
                                          "result": plan.model_dump(mode="json")})
    return plan


@router.get("/plans/{plan_id}", response_model=TripPlan)
async def read_plan(plan_id: UUID, database: Database, user: User):
    """Return the authenticated owner's stored itinerary without running the model again."""
    saved = await get_owned_document(database.trip_plans, plan_id, user)
    return TripPlan.model_validate(saved["result"])


@router.get("/plans/{plan_id}/pdf", response_class=Response, responses={
    200: {"description": "Download the saved itinerary", "content": {
        "application/pdf": {"schema": {"type": "string", "format": "binary"}},
    }},
})
async def download_plan(plan_id: UUID, database: Database, user: User):
    """Render the exact saved plan without another LLM or web request."""
    saved = await get_owned_document(database.trip_plans, plan_id, user)
    plan = TripPlan.model_validate(saved["result"])
    content = await run_in_threadpool(render_plan_pdf, plan)
    return Response(content, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="virasat-trip-{plan_id}.pdf"',
        "Cache-Control": "private, no-store",
    })


@router.get("/capabilities", response_model=Capabilities)
async def capabilities(response: Response, database: Database):
    """Return a fresh or recently probed snapshot of feature readiness."""
    response.headers["Cache-Control"] = "no-store"
    return await get_capabilities(database)


@router.post("/transport/search", response_model=TransportSearchResult)
async def transport_search(request: TransportSearchInput, response: Response, database: Database, user: User):
    """Compare supported journey options and return validated provider results."""
    response.headers["Cache-Control"] = "private, no-store"
    return await search_transport(request, database, user["username"])


@router.post("/hotels/search", response_model=HotelSearchResult)
async def hotels_search(request: HotelSearchInput, response: Response, database: Database, user: User):
    """Compare supported stays and return validated provider results."""
    response.headers["Cache-Control"] = "private, no-store"
    return await search_hotels(request, database, user["username"])


@router.get("/places/suggest", response_model=list[PlaceSuggestion])
async def places_suggest(q: Annotated[str, Query(min_length=1, max_length=120)], response: Response):
    """Public city suggestions; a failed provider never invents a location."""
    response.headers["Cache-Control"] = "no-store"
    return await suggest_places(q.strip())
