# Monument story agent

- `graph.py` runs two steps: search for context, then write a story.
- `tools.py` searches DuckDuckGo, then Bing if needed.
- `providers.py` lists configured providers in order, calls Gemini first, then tries Ollama when fallback is allowed. Each provider makes its HTTP request directly.
- `models.py` checks monument fields, sources and the story response.
- `state.py` defines the values passed between the two graph steps.
- `__init__.py` exposes the functions and models imported by the backend.

The public entry point is `generate_story(monument_data)`. Tests replace network calls through test fixtures; there are no alternate runtime model or search arguments. Tour questions and trip planning share the same provider client. Generated content is not independently verified history.
