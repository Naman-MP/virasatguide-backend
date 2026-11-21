"""Keys for saved monument stories."""

import hashlib
import json
from virasatguide_ai_agent.providers import generation_config


def story_key_for(monument: dict) -> str:
    """Match the story to its monument details and provider/model configuration."""
    config = generation_config()
    context = json.dumps(monument, sort_keys=True, ensure_ascii=False)
    if not config["gemini_model"]:
        # Preserve existing Ollama stories/audio while the Gemini key is pending.
        identity = f"v1|{config['ollama_model'] or ''}|{context}"
    else:
        identity = f"v2|{json.dumps(config, sort_keys=True)}|{context}"
    return hashlib.sha256(identity.encode()).hexdigest()
