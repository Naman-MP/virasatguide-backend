"""Research a destination, then validate itinerary cards and calculate totals."""

import asyncio
import re
from datetime import datetime, timezone
from uuid import uuid4

from ddgs import DDGS
from ddgs.exceptions import DDGSException
from fastapi import HTTPException
from pydantic import ValidationError

from virasatguide_ai_agent.models import Source
from virasatguide_backend.data.monuments import MONUMENT_FIELDS
from .llm import generate_json
from .schemas import Day, Evidence, PlanDraft, PlanInput, TripPlan, plain_text


def search_destination(request: PlanInput) -> list[Evidence]:
    location = " ".join(v for v in [request.destination, request.area, request.city] if v)
    query = f"{location} nearby attractions local food official tourism itinerary"
    if request.location:
        query += f" near {request.location.latitude},{request.location.longitude}"
    for engine in ("duckduckgo", "bing"):
        try:
            results = DDGS(timeout=8).text(query, backend=engine, max_results=5)
        except DDGSException:
            continue
        evidence = []
        seen = set()
        for item in results or []:
            try:
                source = Source(title=item.get("title", "")[:200], url=item.get("href", ""), snippet=item.get("body", "")[:1200])
            except (ValidationError, TypeError):
                continue
            url = str(source.url)
            if not url.startswith("https://") or url in seen:
                continue
            seen.add(url)
            evidence.append(Evidence(id=f"web-{len(evidence)+1}", title=source.title,
                                     text=source.snippet, kind="web_snippet", url=url))
        if evidence:
            return evidence
    return []


def draft_error(draft: PlanDraft, request: PlanInput, sources: list[Evidence]) -> str | None:
    if [day.day for day in draft.days] != list(range(1, request.days + 1)):
        return "Return exactly the requested consecutive days starting with 1."
    by_id = {source.id: source for source in sources}
    order = {"morning": 0, "afternoon": 1, "evening": 2}
    for day in draft.days:
        slots = [order[stop.time_of_day] for stop in day.stops]
        if slots != sorted(slots) or sum(stop.duration_minutes for stop in day.stops) > 720:
            return "Order stops morning to evening and keep visits below 12 hours per day."
        for stop in day.stops:
            if any(key not in by_id for key in stop.evidence_ids):
                return "Use only the exact supplied evidence IDs."
            name = re.sub(r"\W+", " ", stop.place_name.casefold()).strip().removeprefix("the ")
            evidence_text = [re.sub(r"\W+", " ", by_id[key].title.casefold() + " " + by_id[key].text.casefold())
                             for key in stop.evidence_ids]
            if not name or not any(f" {name} " in f" {text} " for text in evidence_text):
                return f"The place name '{stop.place_name}' must appear in its cited source. Use an evidenced place or area name instead of inventing a venue."
    return None


async def create_plan(request: PlanInput, database) -> TripPlan:
    city = request.city or request.destination
    cursor = database.monuments.find({"city": {"$regex": f"^{re.escape(city)}$", "$options": "i"}}, MONUMENT_FIELDS)
    monuments = await cursor.sort("id", 1).limit(20).to_list()
    sources = [Evidence(id=f"monument-{m['id']}", title=m["name"], text=f"{m['name']}, {m['city']}. {m['reference_text']}",
                        kind="monument_reference") for m in monuments]
    web = await asyncio.to_thread(search_destination, request)
    sources += web
    if not sources:
        raise HTTPException(503, "No destination sources are available; specify a city or area and retry")
    instruction = (
        "Create practical itinerary cards using the supplied destination, interests and sources. "
        "Return exactly the requested number of consecutive days starting with 1. Day titles should omit the day number. Write in English using common romanized place names. Every stop must be a real place "
        "whose exact name appears in one or more cited sources. Use an evidenced area name for a food stop if no named restaurant is supported. Never invent URLs, venues, coordinates, distances or opening times. "
        "Prefer attractions and food near the specified area; coordinates are a search hint, not verified geospatial evidence. "
        "Group nearby stops and allow time for travel. Give each day's stops in morning, afternoon, evening order. "
        "Each estimated_cost, transport_cost and accommodation_cost is an integer total for ALL travelers, in the requested currency. "
        "Stop costs include food or admission only; transport and accommodation are separate, counted once per day. "
        "Budget is for the entire trip and all travelers. Try to fit it; never omit real costs just to force a fit. "
        "All prices are estimates, not live quotes. Never claim a booking, distance, business status or price was verified."
    )
    context = {"request": request.model_dump(mode="json"), "sources": [e.model_dump() for e in sources]}
    # One correction attempt handles grounded-name and schedule mistakes without an agent loop.
    for attempt in range(2):
        draft = await generate_json(PlanDraft, instruction, context)
        issue = draft_error(draft, request, sources)
        if issue is None:
            break
        context["validation_feedback"] = issue
    if issue:
        raise HTTPException(502, "The itinerary failed source or schedule validation; please retry")
    days = []
    used = set()
    for day in draft.days:
        for stop in day.stops:
            stop.place_name = plain_text(stop.place_name)
            stop.area = plain_text(stop.area)
            stop.description = plain_text(stop.description)
            used.update(stop.evidence_ids)
        day.title = re.sub(r"^Day\s*\d+\s*[:.\-]\s*", "", plain_text(day.title), flags=re.IGNORECASE)
        total = sum(s.estimated_cost for s in day.stops) + day.transport_cost + day.accommodation_cost
        days.append(Day(**day.model_dump(), estimated_total=total))
    total = sum(day.estimated_total for day in days)
    status = "not_set" if request.budget is None else "within_budget" if total <= request.budget else "over_budget"
    warnings = ["Costs are estimates for all travelers; verify prices, opening hours and transport before travel.",
                "Distances and walking routes are not verified. Check a map before setting out."]
    if not web:
        warnings.append("Web search was unavailable or empty. This plan uses the monument catalogue only.")
    if status == "over_budget":
        warnings.append("The estimated trip cost exceeds your budget. Adjust the trip length or preferences.")
    proximity = "coordinates_unverified" if request.location else "area_search" if request.area else "destination_search"
    return TripPlan(plan_id=uuid4(), created_at=datetime.now(timezone.utc), request=request,
                    title=plain_text(draft.title), summary=plain_text(draft.summary), days=days,
                    estimated_total=total, budget_status=status, sources=[e for e in sources if e.id in used],
                    tips=[plain_text(t)[:500] for t in draft.tips], warnings=warnings, proximity_status=proximity)
