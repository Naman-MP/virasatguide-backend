# VirasatGuide AI agent

A small LangGraph flow for monument narration: `research -> write`. The backend passes a validated monument record from MongoDB. Web search uses DuckDuckGo first and Bing if needed. Ollama writes the story; search results are supporting context, not verified historical facts.

Set `OLLAMA_MODEL` to a model you have installed (for example, a Llama model you pulled with Ollama). `OLLAMA_BASE_URL` defaults to `http://127.0.0.1:11434`. Run Ollama before making a story request. No API key is required for a local Ollama server.

The backend calls `generate_story(monument_data)` when `POST /api/v1/monuments/{id}/story` is requested. It returns `monument_id`, `story`, `sources`, and `research_status`. The backend saves generated stories in MongoDB's `tour_stories` collection and reuses them when the monument details and model are unchanged. The story is normalized to plain text before saving, so a voice provider can be connected separately later.
