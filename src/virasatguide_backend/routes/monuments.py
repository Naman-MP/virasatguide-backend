"""Public discovery endpoints for monument cards."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from virasatguide_backend.data.database import get_database
from virasatguide_backend.data.monuments import list_monuments

router = APIRouter(prefix="/api/v1/monuments", tags=["Monuments"])
Database = Annotated[AsyncDatabase, Depends(get_database)]


class MonumentCard(BaseModel):
    id: int
    name: str
    city: str
    location_tag: str
    cover_image: str


class MonumentPage(BaseModel):
    items: list[MonumentCard]
    total: int
    page: int
    page_size: int


@router.get("", response_model=MonumentPage)
async def get_monuments(
    database: Database,
    search: Annotated[str | None, Query(max_length=100, description="Search name, city, or location tag")] = None,
    city: Annotated[str | None, Query(max_length=100, description="Filter by city")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 12,
) -> MonumentPage:
    """Return a stable page of cards for the discovery grid."""
    try:
        items, total = await list_monuments(
            database, search=search, city=city, page=page, page_size=page_size,
        )
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Monument catalogue is unavailable") from exc
    return MonumentPage(items=items, total=total, page=page, page_size=page_size)
