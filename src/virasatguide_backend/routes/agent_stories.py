"""Generate a tour story for a monument selected from the catalogue."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from virasatguide_ai_agent import StoryResult
from virasatguide_backend.data.database import get_database
from virasatguide_backend.services.stories import get_or_create_story
from virasatguide_backend.routes.auth import get_current_user

router = APIRouter(prefix="/api/v1/monuments", tags=["AI agent"])


@router.post(
    "/{monument_id}/story", response_model=StoryResult,
    responses={
        401: {"description": "Sign in required. Use Swagger's Authorize button or send a bearer token."},
        404: {"description": "Monument not found."},
        503: {"description": "The monument database or configured AI providers are unavailable."},
    },
)
async def create_monument_story(
    monument_id: Annotated[int, Path(ge=1)],
    database=Depends(get_database),
    _user=Depends(get_current_user),
) -> StoryResult:
    return await get_or_create_story(database, monument_id)
