# FastAPI backend

- `main.py` loads settings and connects the routes.
- `routes/` handles login, monuments, stories, audio and health checks.
- `data/` handles MongoDB queries, passwords and tokens.
- `services/` shares story generation and caching between endpoints.
- `travel/` handles tour questions, trip planning and PDF downloads.
- `__init__.py` provides the command that starts the local server.

A request normally goes from a route to its helper, then to MongoDB or an external API. Return values are checked with Pydantic models before the frontend receives them.

## Request lifecycle

1. [main.py](main.py) reads environment settings, installs CORS and Google session handling, and registers the routers. [__init__.py](__init__.py) is the local Uvicorn entry point.
2. The [data layer](data/README.md) opens MongoDB, pings it, prepares indexes and applies the idempotent login-name migration. Requests share that connection.
3. The [route layer](routes/README.md) validates requests, authenticates protected operations, and checks resource ownership where applicable. Validation errors omit the submitted input.
4. The [services](services/README.md) and [travel layer](travel/README.md) handle generation, provider calls, storage and exports. Responses contain records, readiness flags, filtered errors, or binary MP3/PDF bytes.

See the [frontend integration guide](../../docs/frontend-contract.md) for request and response shapes and the [QA handoff](../../docs/frontend-demo-qa.md) for live and isolated checks.
