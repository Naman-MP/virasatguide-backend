"""Turn a saved monument story into MP3 audio with Fish Audio."""

import os
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pymongo.errors import PyMongoError

from virasatguide_ai_agent import StoryResult
from virasatguide_backend.data.database import get_database
from virasatguide_backend.data.monuments import get_monument
from virasatguide_backend.data.stories import story_key_for
from virasatguide_backend.routes.auth import get_current_user

router = APIRouter(prefix="/api/v1/monuments", tags=["Audio"])
FISH_TTS_URL = "https://api.fish.audio/v1/tts"
FISH_MODEL = "s2.1-pro-free"


@router.post(
    "/{monument_id}/audio",
    response_class=Response,
    responses={
        200: {
            "description": "MP3 narration",
            "content": {"audio/mpeg": {"schema": {"type": "string", "format": "binary"}}},
        },
        401: {"description": "Sign in required."},
        404: {"description": "Monument or saved story not found. Generate the story first."},
        502: {"description": "Fish Audio rejected the request or returned invalid audio."},
        503: {"description": "Database or Fish Audio configuration is unavailable."},
        504: {"description": "Fish Audio timed out."},
    },
)
async def create_monument_audio(
    monument_id: Annotated[int, Path(ge=1)],
    database=Depends(get_database),
    _user=Depends(get_current_user),
) -> Response:
    try:
        monument = await get_monument(database, monument_id)
        if monument is None:
            raise HTTPException(404, "Monument not found")
        saved = await database.tour_stories.find_one({"story_key": story_key_for(monument)})
    except PyMongoError as exc:
        raise HTTPException(503, "Story storage is unavailable") from exc
    if saved is None:
        raise HTTPException(404, "Generate the monument story first")

    story = StoryResult.model_validate(saved["result"]).story
    api_key = os.getenv("FISH_AUDIO_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(503, "Set FISH_AUDIO_API_KEY to enable audio")

    body = {"text": story, "format": "mp3"}
    voice_id = os.getenv("FISH_AUDIO_VOICE_ID", "").strip()
    if voice_id:
        body["reference_id"] = voice_id
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            result = await client.post(
                FISH_TTS_URL,
                headers={"Authorization": f"Bearer {api_key}", "model": FISH_MODEL},
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Fish Audio timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Fish Audio is unavailable") from exc
    mp3_header = result.content.startswith(b"ID3") or (
        len(result.content) >= 2 and result.content[0] == 0xFF
        and result.content[1] & 0xE0 == 0xE0
    )
    if result.status_code != 200 or not mp3_header:
        raise HTTPException(502, "Fish Audio could not generate MP3 audio")

    return Response(
        content=result.content,
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'inline; filename="monument-{monument_id}.mp3"',
                 "Cache-Control": "private, no-store"},
    )
