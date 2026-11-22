"""Conservative readiness checks, not just a list of installed routes."""

import asyncio
import time

from pydantic import BaseModel
from virasatguide_ai_agent.providers import generate, generation_config
from virasatguide_backend.data.database import is_database_available
from . import search_providers
from .search import provider_ready


class Capabilities(BaseModel):
    """Expose the readiness flags the frontend uses to enable travel features."""
    transport_search: bool
    hotel_search: bool
    extended_planning: bool
    profile_onboarding: bool


# Per-worker, one-minute cache. No credentials or traveler data are stored here.
_ai_probe = {"config": None, "checked": 0.0, "ready": False}


async def ai_ready() -> bool:
    """Probe model generation with a short, cached request."""
    config = generation_config()
    now = time.monotonic()
    if _ai_probe["config"] == config and now - _ai_probe["checked"] < 60:
        return _ai_probe["ready"]
    ready = False
    if config["gemini_model"] or config["ollama_model"]:
        try:
            text = await asyncio.wait_for(generate("Reply only READY.", "Service readiness check.", max_tokens=128, temperature=0), timeout=6)
            ready = isinstance(text, str) and text.strip().rstrip(".") == "READY"
        except Exception:
            pass
    _ai_probe.update(config=config, checked=now, ready=ready)
    return ready


async def get_capabilities(database) -> Capabilities:
    """Combine database, inventory, and model checks into frontend readiness flags."""
    if not await is_database_available(database):
        return Capabilities(transport_search=False, hotel_search=False, extended_planning=False, profile_onboarding=False)
    transport = [await provider_ready(p) for p in search_providers.transport_providers]
    hotels = [await provider_ready(p) for p in search_providers.hotel_providers]
    return Capabilities(transport_search=any(transport), hotel_search=any(hotels),
                        extended_planning=await ai_ready(), profile_onboarding=True)
