"""Research a destination, then validate itinerary cards and calculate totals."""

import asyncio
import re
from datetime import datetime, timezone
from uuid import uuid4

from ddgs import DDGS
from ddgs.exceptions import DDGSException
from fastapi import HTTPException
from pydantic import ValidationError

from virasatguide_ai_agent.graph import clean_story_text
from virasatguide_ai_agent.models import Source
from virasatguide_backend.data.monuments import MONUMENT_FIELDS
from .llm import generate_json
from .planning_offers import planning_context, apply_offer_costs
from .schemas import Day, Evidence, PlanDraft, PlanInput, TripPlan


PLAN_INSTRUCTION = (
    "Create practical itinerary cards using the supplied destination, interests and sources. "
    "Return exactly the requested number of consecutive days starting with 1. Day titles should omit the day number. Write in English using common romanized place names. Every stop must be a real place "
    "whose exact name appears in one or more cited sources. Use an evidenced area name for a food stop if no named restaurant is supported. Never invent URLs, venues, coordinates, distances or opening times. "
    "Prefer attractions and food near the specified area; coordinates are a search hint, not verified geospatial evidence. "
    "Group nearby stops and allow time for travel. Give each day's stops in morning, afternoon, evening order. "
    "Each estimated_cost, transport_cost and accommodation_cost is an integer total for ALL travelers, in the requested currency. "
    "Stop costs include food or admission only; transport and accommodation are separate, counted once per day. "
    "Budget is for the entire trip and all travelers. Try to fit it; never omit real costs just to force a fit. "
    "All prices are estimates, not live quotes. Never claim a booking, distance, business status or price was verified. "
    "If previous_draft and validation_feedback are supplied, repair that draft and check every stop against its cited sources."
)


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


def place_is_supported(stop, sources_by_id) -> bool:
    """Check whether a stop's name appears in at least one of its cited sources."""
    place_name = re.sub(r"\W+", " ", stop.place_name.casefold()).strip().removeprefix("the ")
    if not place_name:
        return False
    for source_id in stop.evidence_ids:
        source = sources_by_id[source_id]
        source_text = re.sub(r"\W+", " ", source.title.casefold() + " " + source.text.casefold())
        if f" {place_name} " in f" {source_text} ":
            return True
    return False


def draft_error(draft: PlanDraft, request: PlanInput, sources: list[Evidence]) -> str | None:
    if [day.day for day in draft.days] != list(range(1, request.days + 1)):
        return "Return exactly the requested consecutive days starting with 1."
    by_id = {source.id: source for source in sources}
    order = {"morning": 0, "afternoon": 1, "evening": 2}
    issues = []
    for day in draft.days:
        slots = [order[stop.time_of_day] for stop in day.stops]
        visit_minutes = sum(stop.duration_minutes for stop in day.stops)
        if slots != sorted(slots) or visit_minutes > 720:
            return "Order stops morning to evening and keep visits below 12 hours per day."
        for stop in day.stops:
            if any(key not in by_id for key in stop.evidence_ids):
                issues.append("Use only the exact supplied evidence IDs.")
                continue
            if not place_is_supported(stop, by_id):
                issues.append(f"The place name '{stop.place_name}' must appear in its cited source. Use an evidenced place or area name instead of inventing a venue.")
    return " ".join(issues) if issues else None


def build_plan(request: PlanInput, draft: PlanDraft, sources: list[Evidence], has_web_sources: bool) -> TripPlan:
    """Calculate totals and turn the checked draft into the saved plan format."""
    days = []
    used_source_ids = set()
    for day in draft.days:
        for stop in day.stops:
            used_source_ids.update(stop.evidence_ids)
        day.title = re.sub(r"^Day\s*\d+\s*[:.\-]\s*", "", day.title, flags=re.IGNORECASE)
        total = sum(s.estimated_cost for s in day.stops) + day.transport_cost + day.accommodation_cost
        days.append(Day(**day.model_dump(), estimated_total=total))
    total = sum(day.estimated_total for day in days)
    if request.budget is None:
        status = "not_set"
    elif total <= request.budget:
        status = "within_budget"
    else:
        status = "over_budget"
    warnings = ["Costs are estimates for all travelers; verify prices, opening hours and transport before travel.",
                "Distances and walking routes are not verified. Check a map before setting out."]
    if not has_web_sources:
        warnings.append("Web search was unavailable or empty. This plan uses the monument catalogue only.")
    if status == "over_budget":
        warnings.append("The estimated trip cost exceeds your budget. Adjust the trip length or preferences.")
    if request.location:
        proximity = "coordinates_unverified"
    elif request.area:
        proximity = "area_search"
    else:
        proximity = "destination_search"
    return TripPlan(plan_id=uuid4(), created_at=datetime.now(timezone.utc), request=request,
                    title=draft.title, summary=draft.summary, days=days,
                    estimated_total=total, budget_status=status, sources=[e for e in sources if e.id in used_source_ids],
                    tips=[clean_story_text(t)[:500] for t in draft.tips], warnings=warnings, proximity_status=proximity)


async def create_plan(request: PlanInput, database, *, owner: str | None = None) -> TripPlan:
    extended = bool(request.origin or request.stops or request.departure_date or request.travel_mode or request.hotel_preferences)
    selected, offer_context, offer_warnings = None, {}, []
    if extended:
        selected, offer_context, offer_warnings = await planning_context(request, database, owner)
    city = request.city or request.destination.split(",", 1)[0].strip()
    cities = [city] + [place.label.split(",", 1)[0].strip() for place in request.stops]
    sources, seen_monuments = [], set()
    for city_name in dict.fromkeys(cities):
        cursor = database.monuments.find({"city": {"$regex": f"^{re.escape(city_name)}$", "$options": "i"}}, MONUMENT_FIELDS)
        monuments = await cursor.sort("id", 1).limit(4 if request.stops else 20).to_list()
        for monument in monuments:
            if monument["id"] in seen_monuments:
                continue
            seen_monuments.add(monument["id"])
            sources.append(Evidence(id=f"monument-{monument['id']}", title=monument["name"],
                text=f"{monument['name']}, {monument['city']}. {monument['reference_text']}", kind="monument_reference"))
    web, seen_urls = [], set()
    destinations = [request] + [request.model_copy(update={"destination": p.label, "city": None, "area": None, "location": None}) for p in request.stops]
    for destination in destinations:
        for item in await asyncio.to_thread(search_destination, destination):
            if item.url not in seen_urls:
                seen_urls.add(item.url)
                web.append(item.model_copy(update={"id": f"web-{len(web)+1}"}))
    sources += web
    if selected is not None and selected.hotel:
        hotel = selected.hotel
        sources.append(Evidence(id=f"hotel-{hotel.id}", title=hotel.hotel_name,
                text=f"{hotel.hotel_name}, {hotel.location}. Provider: {hotel.provider}.", kind="provider_offer", url=hotel.booking_url))
    if not sources:
        raise HTTPException(503, "No destination sources are available; specify a city or area and retry")

    context = {"request": request.model_dump(mode="json"), "sources": [e.model_dump() for e in sources], **offer_context}
    instruction = PLAN_INSTRUCTION
    if extended:
        instruction += " Consider the supplied departure/return dates, intermediate destinations in route order, transport mode and hotel preferences. Never invent providers, tickets, fares, availability or booking URLs. Unselected offers are context, not bookings. Keep daily transport_cost for local travel only; selected intercity transport is added separately by the server. Hotel estimates on nights covered by a selected stay total are replaced by the server. All remaining costs are estimates."
    # One correction attempt handles grounded-name and schedule mistakes without an agent loop.
    for _ in range(2):
        draft = await generate_json(PlanDraft, instruction, context)
        issue = draft_error(draft, request, sources)
        if issue is None:
            break
        context["validation_feedback"] = issue
        context["previous_draft"] = draft.model_dump(mode="json")
    if issue:
        raise HTTPException(502, "The itinerary failed source or schedule validation; please retry")
    plan = build_plan(request, draft, sources, bool(web))
    return apply_offer_costs(plan, selected, offer_warnings) if selected is not None else plan
