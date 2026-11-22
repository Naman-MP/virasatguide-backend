# Backend checks

Run `uv run pytest -q` from the project root. These tests use fake network calls and databases, so they do not need private credentials or create real user records.

The files cover login and Google callbacks, monument search, model fallback, story caching, MP3 responses, tour history, plan validation, ownership and PDF output. `conftest.py` supplies fixed test settings. Small fake database classes implement only the MongoDB operations needed by those tests.

For a separate full check against real services, use `scripts/smoke_backend.py` as described in the root README.


`test_credentials.py` covers same-account Google/password sign-in, race conditions, validation, migration and privacy. `test_places.py` covers autocomplete. Two optional tests use an isolated **local** MongoDB URI; they reject a non-loopback test URI. `test_frontend_contract.py` runs the actual frontend `src/api.js` and `src/auth.js` through Node against a local FastAPI server and real MongoDB. Other external services are test fixtures. It reads the frontend without editing it. See `docs/frontend-contract.md` for commands and the tested frontend snapshot.
