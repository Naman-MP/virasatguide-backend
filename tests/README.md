# Backend checks

Run `uv run pytest -q` from the project root. These tests use fake network calls and databases, so they do not need private credentials or create real user records.

The files cover login and Google callbacks, monument search, model fallback, story caching, MP3 responses, tour history, plan validation, ownership and PDF output. `conftest.py` supplies fixed test settings. Small fake database classes implement only the MongoDB operations needed by those tests.

For a separate full check against real services, use `scripts/smoke_backend.py` as described in the root README.
