# FastAPI backend

- `main.py` loads settings and connects the routes.
- `routes/` handles login, monuments, stories, audio and health checks.
- `data/` handles MongoDB queries, passwords and tokens.
- `services/` shares story generation and caching between endpoints.
- `travel/` handles tour questions, trip planning and PDF downloads.
- `__init__.py` provides the command that starts the local server.

A request normally goes from a route to its helper, then to MongoDB or an external API. Return values are checked with Pydantic models before the frontend receives them.
