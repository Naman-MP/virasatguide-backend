"""Search licensed adapters and store short-lived, user-owned offer snapshots."""

import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4


from . import search_providers
from .search_models import HotelOption, HotelSearchResult, TransportOption, TransportSearchResult

OFFER_LIFETIME = timedelta(minutes=15)


async def provider_ready(provider) -> bool:
    try:
        return await asyncio.wait_for(provider.is_ready(), timeout=5) is True
    except Exception:
        # Never expose provider errors or credentials in responses/logs.
        return False


def provider_supports(provider, request) -> bool:
    try:
        return provider.supports(request) is True
    except Exception:
        return False


def check_offer(offer, provider, currency):
    if offer.provider != provider.name:
        raise ValueError("Provider attribution does not match the adapter")
    age = datetime.now(timezone.utc) - offer.retrieved_at
    if age < timedelta(seconds=-30) or age > OFFER_LIFETIME:
        raise ValueError("The offer is stale or has a future timestamp")
    prices = [offer.price] if isinstance(offer, TransportOption) else [offer.nightly_price, offer.total_price]
    if any(price and price.currency != currency for price in prices):
        raise ValueError("Offer currency does not match the request")
    if offer.booking_url:
        host = urlsplit(offer.booking_url).hostname
        if not any(host == allowed or host.endswith("." + allowed) for allowed in provider.booking_hosts):
            raise ValueError("Booking URL is outside the verified provider hosts")
    # Public option IDs are generated here, never accepted from the browser/provider.
    return offer.model_copy(update={"id": uuid4()})


def price_order(price):
    if price is None:
        return (2, 0)
    return (0 if price.type == "live" else 1, price.amount)


async def store_offers(database, owner, request, response, kind):
    for offer in response.results:
        await database.travel_offers.insert_one({
            "_id": str(offer.id), "owner": owner, "kind": kind,
            "request": request.model_dump(mode="json"), "offer": offer.model_dump(mode="json"),
            "search_id": str(response.search_id),
            "expires_at": min(response.searched_at, offer.retrieved_at) + OFFER_LIFETIME,
        })


async def search_transport(request, database, owner) -> TransportSearchResult:
    results, warnings = [], []
    had_failure = False
    for provider in search_providers.transport_providers:
        modes = [mode for mode in request.modes if mode in provider.modes]
        if not modes:
            continue
        query = request.model_copy(update={"modes": modes})
        if len(request.stops) > provider.max_stops or not provider_supports(provider, query):
            warnings.append("A transport provider does not support the complete requested route.")
            continue
        if not await provider_ready(provider):
            warnings.append("A transport provider is unavailable.")
            had_failure = True
            continue
        try:
            offers = await asyncio.wait_for(provider.search(query), timeout=20)
            for raw in offers[:100]:
                offer = TransportOption.model_validate(raw)
                if offer.mode not in modes:
                    raise ValueError("Unexpected transport mode")
                if offer.departure_at and offer.departure_at.date() != request.departure_date:
                    raise ValueError("Departure date does not match")
                results.append(check_offer(offer, provider, request.currency))
        except Exception:
            warnings.append("A transport provider returned an error or invalid offer; that inventory is incomplete.")
            had_failure = True
    unavailable = [mode for mode in request.modes if not any(offer.mode == mode for offer in results)]
    if not results:
        status = "unavailable"
        warnings.append("No verified transport offers are available for this request. No prices or booking links were invented.")
    else:
        status = "partial" if unavailable or had_failure else "complete"
    if results and any(o.price is None or o.price.type == "estimate" or o.booking_url is None for o in results):
        status = "partial"
    results.sort(key=lambda offer: price_order(offer.price))
    if any(offer.price is None or offer.price.type == "estimate" for offer in results):
        warnings.append("Some options have missing prices or provider estimates rather than live quotes.")
    response = TransportSearchResult(status=status, currency=request.currency, results=results,
                                     unavailable_modes=unavailable, warnings=list(dict.fromkeys(warnings)))
    await store_offers(database, owner, request, response, "transport")
    return response


async def search_hotels(request, database, owner) -> HotelSearchResult:
    results, warnings = [], []
    had_failure = False
    for provider in search_providers.hotel_providers:
        if not provider_supports(provider, request) or not await provider_ready(provider):
            warnings.append("A hotel provider is unavailable or does not support this stay.")
            had_failure = True
            continue
        try:
            offers = await asyncio.wait_for(provider.search(request), timeout=20)
            for raw in offers[:100]:
                offer = HotelOption.model_validate(raw)
                if request.star_ratings and offer.star_rating not in request.star_ratings:
                    continue
                results.append(check_offer(offer, provider, request.currency))
        except Exception:
            warnings.append("A hotel provider returned an error or invalid offer; that inventory is incomplete.")
            had_failure = True
    status = "partial" if results and had_failure else "complete" if results else "unavailable"
    if not results:
        warnings.append("No verified hotel inventory is available for this stay. No prices or booking links were invented.")
    if results and any(o.total_price is None or o.total_price.type == "estimate" or o.booking_url is None for o in results):
        status = "partial"
    results.sort(key=lambda offer: price_order(offer.total_price))
    if any(offer.total_price is None or offer.total_price.type == "estimate" for offer in results):
        warnings.append("Some hotel totals are missing or are provider estimates rather than live quotes.")
    response = HotelSearchResult(status=status, results=results, warnings=list(dict.fromkeys(warnings)))
    await store_offers(database, owner, request, response, "hotel")
    return response
