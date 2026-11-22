"""Generate or reuse a saved monument story for both story and tour routes."""

from datetime import datetime, timezone

from fastapi import HTTPException
from pymongo.errors import PyMongoError

from virasatguide_ai_agent import StoryResult, generate_story
from virasatguide_ai_agent.graph import AgentError
from virasatguide_ai_agent.providers import generation_config
from virasatguide_backend.data.monuments import get_monument
from virasatguide_backend.data.stories import story_key_for


async def get_or_create_story(database, monument_id: int, *, monument=None) -> StoryResult:
    try:
        # A tour session already fetched its monument; other callers look it up here.
        if monument is None:
            monument = await get_monument(database, monument_id)
        if monument is None:
            raise HTTPException(404, "Monument not found")
        story_key = story_key_for(monument)
        saved = await database.tour_stories.find_one({"story_key": story_key})
        if saved:
            return StoryResult.model_validate(saved["result"])

        result = await generate_story(monument)
        document = {
            "story_key": story_key,
            "monument_id": monument_id,
            "generation_config": generation_config(),
            "created_at": datetime.now(timezone.utc),
            "result": result.model_dump(mode="json"),
        }
        await database.tour_stories.update_one(
            {"story_key": story_key}, {"$setOnInsert": document}, upsert=True,
        )
        return result
    except PyMongoError as exc:
        raise HTTPException(503, "Story storage is unavailable") from exc
    except AgentError as exc:
        raise HTTPException(503, str(exc)) from exc
