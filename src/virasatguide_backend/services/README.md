# Shared story operation

`stories.py` looks up a monument, returns its cached story if available, or generates and saves a new story. Both the story endpoint and a new tour session use this function so their behavior stays consistent.

## Story cache and concurrent generation

The [story service](stories.py) loads the monument and computes a key from the monument and model configuration. A cache hit returns the stored result. A cache miss runs the [AI agent](../../../ai_agent/src/virasatguide_ai_agent/README.md) and upserts the narration. Several simultaneous requests may start generation. Each successful caller returns the one committed narration, including callers that lost the write race. Story requests and private tour creation share this service. Audio uses the same cache key and reads the committed text.
