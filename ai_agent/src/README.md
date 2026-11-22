# AI package source

`virasatguide_ai_agent/` is the Python package imported by the backend. The LangGraph flow and model calls live there. Install it with the rest of the project using `uv sync` from the project root.

The [agent package](virasatguide_ai_agent/README.md) contains its workflow, state and response models, web research and model providers. The backend imports it as a workspace dependency.
