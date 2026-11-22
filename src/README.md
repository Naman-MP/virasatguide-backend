# Backend source

The FastAPI application is in `virasatguide_backend/`. Its `main.py` connects the routes, database and browser access settings. The separate `ai_agent/` package at the project root generates stories.

The [backend package](virasatguide_backend/README.md) serves the [OpenAPI contract](../docs/openapi.json). Its routers compose validation, authorization, MongoDB queries and the separate [AI agent package](../ai_agent/src/README.md).
