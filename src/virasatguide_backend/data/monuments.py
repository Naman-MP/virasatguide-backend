"""MongoDB queries for the public monument catalogue."""

import re

from pymongo.asynchronous.database import AsyncDatabase


async def list_monuments(
    database: AsyncDatabase, *, search: str | None, city: str | None,
    page: int, page_size: int,
) -> tuple[list[dict], int]:
    filters: dict = {}
    if search:
        term = re.escape(search.strip())
        if term:
            filters["$or"] = [
                {field: {"$regex": term, "$options": "i"}}
                for field in ("name", "city", "location_tag")
            ]
    if city:
        filters["city"] = {"$regex": f"^{re.escape(city.strip())}$", "$options": "i"}

    collection = database.monuments
    total = await collection.count_documents(filters)
    cursor = collection.find(filters, {"_id": 0, "id": 1, "name": 1, "city": 1,
                                       "location_tag": 1, "cover_image": 1})
    items = await cursor.sort("id", 1).skip((page - 1) * page_size).limit(page_size).to_list()
    return items, total
