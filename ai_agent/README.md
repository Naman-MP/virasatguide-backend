# VirasatGuide AI agent

A small LangGraph flow for monument narration: `research -> write`. The backend passes a validated monument record from MongoDB. Web search uses DuckDuckGo first and Bing if needed. Gemini writes the story, with Ollama as the fallback. Search snippets are supporting context, not verified historical facts.

Set `GEMINI_API_KEY` and optionally `GEMINI_MODEL` (default `gemini-3.5-flash-lite`). Configure `OLLAMA_MODEL`, `OLLAMA_BASE_URL` and optional `OLLAMA_API_KEY` for the fallback. Without a Gemini key, the agent uses Ollama directly. The development setup uses `qwen3:8b`, `http://127.0.0.1:11434` and `OLLAMA_THINK=false`. Vercel needs a hosted Ollama address for fallback; localhost does not reach your Mac.

`providers.py` makes ordinary HTTP model calls and validates structured output. Both the story graph and backend travel JSON adapter share it. The backend calls `generate_story(monument_data)` and receives the same `monument_id`, `story`, `sources`, and `research_status` fields. Stories are cleaned into plain text and cached in MongoDB by monument and provider/model policy. Audio generation remains a separate Fish Audio endpoint.

See the [travel API guide](../docs/travel-api.md) for production setup, fallback behavior and frontend endpoint payloads. Provider keys belong only in backend environment settings.

For a file-by-file explanation, see [the source folder guide](src/virasatguide_ai_agent/README.md). The story flow has no alternate model/search arguments; tests replace network calls with fixtures.
