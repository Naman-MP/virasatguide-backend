"""Generate or reuse a saved monument story for both story and tour routes."""

from datetime import datetime, timezone

from fastapi import HTTPException
from pymongo.errors import PyMongoError

from virasatguide_ai_agent import StoryResult, generate_story
from virasatguide_ai_agent.graph import AgentError
from virasatguide_ai_agent.providers import generation_config
from virasatguide_backend.data.monuments import get_monument
from virasatguide_backend.data.stories import story_key_for


async def get_or_create_story(database, monument_id: int, *, generator=generate_story, monument=None) -> StoryResult:
    try:
        if monument is None:
            monument = await get_monument(database, monument_id)
    except PyMongoError as exc:
        raise HTTPException(503, "Monument catalogue is unavailable") from exc
    if monument is None:
        raise HTTPException(404, "Monument not found")
    story_key = story_key_for(monument)
    try:
        saved = await database.tour_stories.find_one({"story_key": story_key})
        if saved:
            return StoryResult.model_validate(saved["result"])
    except PyMongoError as exc:
        raise HTTPException(503, "Story storage is unavailable") from exc

    try:
        result = await generator(monument)
    except AgentError as exc:
        raise HTTPException(503, str(exc)) from exc
    document = {
        "story_key": story_key,
        "monument_id": monument_id,
        "generation_config": generation_config(),
        "created_at": datetime.now(timezone.utc),
        "result": result.model_dump(mode="json"),
    }
    try:
        await database.tour_stories.update_one(
            {"story_key": story_key}, {"$setOnInsert": document}, upsert=True,
        )
    except PyMongoError as exc:
        raise HTTPException(503, "Story storage is unavailable") from exc
    return result
