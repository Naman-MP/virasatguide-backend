"""MongoDB queries for the public monument catalogue."""

import re
import unicodedata

from pymongo.asynchronous.database import AsyncDatabase

MONUMENT_FIELDS = {"_id": 0, "id": 1, "name": 1, "city": 1, "location_tag": 1,
                   "cover_image": 1, "reference_text": 1}


async def list_monuments(
    database: AsyncDatabase, *, search: str | None, city: str | None,
    page: int, page_size: int,
) -> tuple[list[dict], int]:
    filters: dict = {}
    term = re.escape((search or "").strip())
    if term:
        filters["$or"] = [
            {field: {"$regex": term, "$options": "i"}}
            for field in ("name", "city", "location_tag")
        ]
    if city:
        filters["city"] = {"$regex": f"^{re.escape(city.strip())}$", "$options": "i"}

    collection = database.monuments
    total = await collection.count_documents(filters)
    cursor = collection.find(filters, MONUMENT_FIELDS)
    items = await cursor.sort("id", 1).skip((page - 1) * page_size).limit(page_size).to_list()
    return items, total


async def get_monument(database: AsyncDatabase, monument_id: int) -> dict | None:
    """Fetch one catalogue entry by its public numeric ID."""
    return await database.monuments.find_one({"id": monument_id}, MONUMENT_FIELDS)


def slugify(value: str) -> str:
    """Turn a display name into a URL segment."""
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


async def get_monument_by_slugs(
    database: AsyncDatabase, city_slug: str, monument_slug: str,
) -> dict | None:
    """Match a city and monument name without relying on stored slug fields."""
    city_name = city_slug.replace("-", " ")
    cursor = database.monuments.find(
        {"city": {"$regex": f"^{re.escape(city_name)}$", "$options": "i"}},
        MONUMENT_FIELDS,
    )
    for monument in await cursor.sort("id", 1).to_list():
        if slugify(monument["city"]) == city_slug and slugify(monument["name"]) == monument_slug:
            return monument
    return None
