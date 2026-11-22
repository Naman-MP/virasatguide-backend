"""Public discovery endpoints for monument cards."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from virasatguide_backend.data.database import get_database
from virasatguide_backend.data.monuments import get_monument, get_monument_by_slugs, list_monuments

router = APIRouter(prefix="/api/v1/monuments", tags=["Monuments"])
Database = Annotated[AsyncDatabase, Depends(get_database)]


class MonumentCard(BaseModel):
    """Public discovery and detail fields for a single monument."""
    id: int
    name: str
    city: str
    location_tag: str
    cover_image: str
    reference_text: str


class MonumentPage(BaseModel):
    """Search results plus total, page number, and page size for pagination."""
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


@router.get("/{monument_id}", response_model=MonumentCard)
async def get_monument_detail(
    monument_id: Annotated[int, Path(ge=1)], database: Database,
) -> MonumentCard:
    """Return one monument for a selected discovery card."""
    try:
        monument = await get_monument(database, monument_id)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Monument catalogue is unavailable") from exc
    if monument is None:
        raise HTTPException(status_code=404, detail="Monument not found")
    return MonumentCard.model_validate(monument)


@router.get("/{city_slug}/{monument_slug}", response_model=MonumentCard)
async def get_monument_by_location(
    city_slug: Annotated[str, Path(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)],
    monument_slug: Annotated[str, Path(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)],
    database: Database,
) -> MonumentCard:
    """Return a card by its city and monument URL names."""
    try:
        monument = await get_monument_by_slugs(database, city_slug, monument_slug)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Monument catalogue is unavailable") from exc
    if monument is None:
        raise HTTPException(status_code=404, detail="Monument not found in this city")
    return MonumentCard.model_validate(monument)
