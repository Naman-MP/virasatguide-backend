"""Authenticated travel endpoints. Every saved resource belongs to its creator."""

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pymongo.errors import PyMongoError

from virasatguide_backend.data.database import get_database
from virasatguide_backend.data.monuments import get_monument
from virasatguide_backend.routes.auth import get_current_user
from virasatguide_backend.services.stories import get_or_create_story
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
    try:
        yield database
    except PyMongoError as exc:
        raise HTTPException(503, "Travel storage is unavailable") from exc


Database = Annotated[object, Depends(storage)]
User = Annotated[dict, Depends(get_current_user)]


async def owned(collection, resource_id: UUID, user: dict):
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
    saved = await owned(database.tour_sessions, session_id, user)
    saved["view"]["history"] = saved["view"]["history"][-CHAT_HISTORY_LIMIT:]
    return TourSession.model_validate(saved["view"])


@router.post("/tour-sessions/{session_id}/questions", response_model=Answer)
async def ask_question(session_id: UUID, request: QuestionInput, database: Database, user: User):
    saved = await owned(database.tour_sessions, session_id, user)
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
    update = await database.tour_sessions.update_one(
        {"_id": str(session_id), "owner": user["username"], "version": saved["version"]},
        {"$push": {"view.history": {"$each": [answer.model_dump(mode="json")], "$slice": -CHAT_HISTORY_LIMIT}},
         "$inc": {"version": 1}},
    )
    if update.modified_count != 1:
        raise HTTPException(409, "Another question changed this session; retry with the same request_id")
    return answer


@router.post("/plans", response_model=TripPlan, status_code=201)
async def plan_trip(request: PlanInput, database: Database, user: User):
    """Research a destination and save itinerary cards with budget estimates."""
    plan = await create_plan(request, database)
    await database.trip_plans.insert_one({"_id": str(plan.plan_id), "owner": user["username"],
                                          "result": plan.model_dump(mode="json")})
    return plan


@router.get("/plans/{plan_id}", response_model=TripPlan)
async def read_plan(plan_id: UUID, database: Database, user: User):
    saved = await owned(database.trip_plans, plan_id, user)
    return TripPlan.model_validate(saved["result"])


@router.get("/plans/{plan_id}/pdf", response_class=Response, responses={
    200: {"description": "Download the saved itinerary", "content": {
        "application/pdf": {"schema": {"type": "string", "format": "binary"}},
    }},
})
async def download_plan(plan_id: UUID, database: Database, user: User):
    """Render the exact saved plan without another LLM or web request."""
    saved = await owned(database.trip_plans, plan_id, user)
    plan = TripPlan.model_validate(saved["result"])
    content = await run_in_threadpool(render_plan_pdf, plan)
    return Response(content, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="virasat-trip-{plan_id}.pdf"',
        "Cache-Control": "private, no-store",
    })
