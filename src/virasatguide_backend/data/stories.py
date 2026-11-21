"""Keys for saved monument stories."""

import hashlib
import json
import os


def story_key_for(monument: dict) -> str:
    """Match the story to its monument details and Ollama model."""
    model = os.getenv("OLLAMA_MODEL", "").strip()
    context = json.dumps(monument, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f"v1|{model}|{context}".encode()).hexdigest()
