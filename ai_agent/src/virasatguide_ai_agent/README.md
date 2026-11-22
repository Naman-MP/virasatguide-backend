# Monument story agent

- `graph.py` runs two steps: search for context, then write a story.
- `tools.py` searches DuckDuckGo, then Bing if needed.
- `providers.py` lists configured providers in order, calls Gemini first, then tries Ollama when fallback is allowed. Each provider makes its HTTP request directly.
- `models.py` checks monument fields, sources and the story response.
- `state.py` defines the values passed between the two graph steps.
- `__init__.py` exposes the functions and models imported by the backend.

The public entry point is `generate_story(monument_data)`. Tests replace network calls through test fixtures; there are no alternate runtime model or search arguments. Tour questions and trip planning share the same provider client. Generated content is not independently verified history.

## Research and generation

1. [models.py](models.py) validates monument context and public source links. [state.py](state.py) defines the graph's intermediate data.
2. [graph.py](graph.py) calls [tools.py](tools.py) to search for heritage sources. Search starts with DuckDuckGo and can fall back to Bing. With no suitable results, catalogue evidence still supports narration.
3. [providers.py](providers.py) tries Gemini when configured and can fall back to Ollama for eligible failures. Output parsing checks the requested format and omits reasoning content. Model refusals stop fallback.
4. The graph cleans the narration and returns its sources. The backend owns persistence, authorization and additional validation for tour answers and itinerary drafts.
