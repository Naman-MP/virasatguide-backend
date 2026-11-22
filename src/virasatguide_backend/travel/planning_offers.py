"""Resolve only owned, fresh offers; separate provider quotes from AI estimates."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from pydantic import ValidationError

from .search import search_hotels, search_transport
from .search_models import (CostSummary, HotelOption, HotelSearchInput, PlanningOffers,
                            TransportOption, TransportSearchInput)


def same_label(a, b):
    return " ".join(a.casefold().split()) == " ".join(b.casefold().split())


async def owned_offer(database, owner, offer_id, kind):
    saved = await database.travel_offers.find_one({
        "_id": str(offer_id), "owner": owner, "kind": kind,
        "expires_at": {"$gt": datetime.now(timezone.utc)},
    })
    if saved is None:
        raise HTTPException(404, "Selected offer is unavailable or expired for this user; search again")
    return saved


async def planning_context(request, database, owner):
    selected = PlanningOffers()
    context, warnings = {}, []
    if request.selected_transport_id:
        saved = await owned_offer(database, owner, request.selected_transport_id, "transport")
        try:
            query = TransportSearchInput.model_validate(saved["request"])
            offer = TransportOption.model_validate(saved["offer"])
        except ValidationError as exc:
            raise HTTPException(409, "Selected transport is no longer valid; search again") from exc
        if offer.price and offer.price.currency != request.currency:
            raise HTTPException(409, "Selected transport currency does not match")
        if (not same_label(query.origin.label, request.origin.label)
            or not same_label(query.destination.label, request.destination)
            or [p.label.casefold() for p in query.stops] != [p.label.casefold() for p in request.stops]
            or query.departure_date != request.departure_date or query.return_date != request.return_date
            or query.travelers != request.travelers or query.currency != request.currency
            or (request.travel_mode and offer.mode != request.travel_mode)):
            raise HTTPException(409, "Selected transport does not match this journey; search again")
        selected.transport = offer
    elif request.origin and request.departure_date:
        query = TransportSearchInput(origin=request.origin, destination={"label": request.destination},
            stops=request.stops, departure_date=request.departure_date, return_date=request.return_date,
            travelers=request.travelers, currency=request.currency,
            modes=[request.travel_mode] if request.travel_mode else ["car", "train", "flight", "bus"])
        found = await search_transport(query, database, owner)
        context["available_transport"] = [o.model_dump(mode="json") for o in found.results[:10]]
        warnings += found.warnings
    if request.selected_hotel_id:
        saved = await owned_offer(database, owner, request.selected_hotel_id, "hotel")
        try:
            query = HotelSearchInput.model_validate(saved["request"])
            offer = HotelOption.model_validate(saved["offer"])
        except ValidationError as exc:
            raise HTTPException(409, "Selected hotel is no longer valid; search again") from exc
        if any(p and p.currency != request.currency for p in [offer.nightly_price, offer.total_price]):
            raise HTTPException(409, "Selected hotel currency does not match")
        last_day = request.return_date or request.departure_date + timedelta(days=request.days)
        prefs = request.hotel_preferences
        if (not same_label(query.destination, request.destination) or query.travelers != request.travelers
            or query.currency != request.currency or query.check_in < request.departure_date
            or query.check_out > last_day or (prefs and query.rooms != prefs.rooms)
            or (prefs and prefs.star_ratings and offer.star_rating not in prefs.star_ratings)):
            raise HTTPException(409, "Selected hotel does not match this stay; search again")
        selected.hotel = offer
        selected.hotel_rooms = query.rooms
        selected.hotel_check_in, selected.hotel_check_out = query.check_in, query.check_out
    elif request.hotel_preferences and request.departure_date:
        checkout = request.return_date or request.departure_date + timedelta(days=request.days)
        if checkout <= request.departure_date:
            warnings.append("No overnight stay was requested; hotel search was skipped.")
        else:
            query = HotelSearchInput(destination=request.destination, check_in=request.departure_date,
                check_out=checkout, travelers=request.travelers, currency=request.currency,
                **request.hotel_preferences.model_dump())
            found = await search_hotels(query, database, owner)
            context["available_hotels"] = [o.model_dump(mode="json") for o in found.results[:10]]
            warnings += found.warnings
    context["selected_offers"] = selected.model_dump(mode="json")
    return selected, context, warnings


def apply_offer_costs(plan, selected: PlanningOffers, warnings: list[str]):
    prices = []
    if selected.transport:
        prices.append(selected.transport.price)
    if selected.hotel:
        prices.append(selected.hotel.total_price)
        # A selected full-stay total replaces estimates only on covered nights.
        if selected.hotel.total_price is not None:
            for day in plan.days:
                visit_date = plan.request.departure_date + timedelta(days=day.day - 1)
                if selected.hotel_check_in <= visit_date < selected.hotel_check_out:
                    day.accommodation_cost = 0
                    day.estimated_total = sum(s.estimated_cost for s in day.stops) + day.transport_cost
    itinerary = sum(day.estimated_total for day in plan.days)
    confirmed = sum((Decimal(str(p.amount)) for p in prices if p and p.type == "live"), Decimal(0))
    estimates = sum((Decimal(str(p.amount)) for p in prices if p and p.type == "estimate"), Decimal(0))
    total = Decimal(itinerary) + confirmed + estimates
    missing_transport = bool(plan.request.origin and selected.transport is None)
    wants_hotel = bool(plan.request.hotel_preferences and (
        not plan.request.departure_date or not plan.request.return_date or plan.request.return_date > plan.request.departure_date))
    missing_hotel = wants_hotel and selected.hotel is None
    plan.selected_offers = selected
    plan.cost_summary = CostSummary(currency=plan.request.currency, itinerary_estimate=itinerary,
        confirmed_total=float(confirmed), selected_estimate_total=float(estimates), trip_total=float(total),
        costs_complete=all(p is not None for p in prices) and not missing_transport and not missing_hotel)
    plan.estimated_total = int(total) if total == int(total) else float(total)
    if plan.request.budget is not None:
        plan.budget_status = "within_budget" if total <= plan.request.budget else "over_budget"
    plan.warnings = [w for w in plan.warnings if w != "The estimated trip cost exceeds your budget. Adjust the trip length or preferences."]
    if plan.budget_status == "over_budget":
        plan.warnings.append("The estimated trip cost exceeds your budget. Adjust the trip length or preferences.")
    plan.warnings += warnings
    if missing_transport:
        plan.warnings.append("No transport offer is selected; journey fares are not included in the budget total.")
    if missing_hotel:
        plan.warnings.append("No hotel offer is selected; accommodation figures remain unverified itinerary estimates.")
    if prices:
        plan.warnings.append("Provider quotes are snapshots at retrieved_at, not bookings. Recheck availability and final charges with the provider.")
    if any(p is None for p in prices):
        plan.warnings.append("Selected offers have unknown totals. The budget comparison excludes those amounts and is incomplete.")
    plan.warnings = list(dict.fromkeys(plan.warnings))
    return plan
