"""Small worldwide city lookup using the documented Open-Meteo/GeoNames API."""

import re

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
ATTRIBUTION = "Open-Meteo / GeoNames"


class PlaceSuggestion(BaseModel):
    """Public location label, coordinates, and source attribution."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str
    label: str
    name: str
    country: str
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    provider: str = ATTRIBUTION
    source_url: str = "https://open-meteo.com/en/docs/geocoding-api"


def display_text(value) -> str:
    """Validate plain display text before accepting a geocoder label or query."""
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("Invalid place label")
    if re.search(r"[<>\x00-\x1f\x7f]", value):
        raise ValueError("Invalid place label")
    return value.strip()


async def suggest_places(query: str) -> list[PlaceSuggestion]:
    """Query Open-Meteo and keep up to six usable, distinct locations."""
    try:
        query = display_text(query)
    except ValueError as exc:
        raise HTTPException(422, "Enter a plain location name") from exc
    if len(query) < 2:
        return []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6, connect=3)) as client:
            response = await client.get(GEOCODING_URL,
                params={"name": query, "count": 6, "language": "en", "format": "json"})
        response.raise_for_status()
        data = response.json()
        results = data.get("results", []) if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise ValueError("Invalid geocoding output")
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Location search timed out; enter the location manually") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Location search is unavailable; enter the location manually") from exc
    except ValueError as exc:
        raise HTTPException(502, "Location search returned invalid data; enter the location manually") from exc
    suggestions, seen = [], set()
    for item in results[:6]:
        try:
            name, country = display_text(item["name"]), display_text(item["country"])
            parts = [name]
            if item.get("admin1"):
                region = display_text(item["admin1"])
                if region != name:
                    parts.append(region)
            parts.append(country)
            place = PlaceSuggestion(id=f"geonames:{int(item['id'])}", label=", ".join(parts),
                name=name, country=country, country_code=item["country_code"],
                latitude=item["latitude"], longitude=item["longitude"])
        except (KeyError, TypeError, ValueError, ValidationError):
            continue
        if place.id not in seen:
            suggestions.append(place)
            seen.add(place.id)
    if results and not suggestions:
        raise HTTPException(502, "Location search returned invalid places; enter the location manually")
    return suggestions
