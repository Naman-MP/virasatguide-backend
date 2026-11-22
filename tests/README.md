# Backend checks

Run `uv run pytest -q` from the project root. These tests use fake network calls and databases, so they do not need private credentials or create real user records.

The files cover login and Google callbacks, monument search, model fallback, story caching, MP3 responses, tour history, plan validation, ownership and PDF output. `conftest.py` supplies fixed test settings. Small fake database classes implement only the MongoDB operations needed by those tests.

For a separate full check against real services, use `scripts/smoke_backend.py` as described in the root README.


`test_credentials.py` covers same-account Google/password sign-in, race conditions, validation, migration and privacy. `test_places.py` covers autocomplete. Two optional tests use an isolated **local** MongoDB URI; they reject a non-loopback test URI. `test_frontend_contract.py` runs the actual frontend `src/api.js` and `src/auth.js` through Node against a local FastAPI server and real MongoDB. Other external services are test fixtures. It reads the frontend without editing it. See `docs/frontend-contract.md` for commands and the tested frontend snapshot.

## What each test group covers

| Files | Checks |
| --- | --- |
| test_auth.py, test_jwt.py, test_google_oauth.py | Signup, invalid and expired tokens, Google redirects and signed identity fixtures, cookies, CORS, logout |
| test_credentials.py, test_credentials_mongo.py | First-time credential setup, legacy users, ambiguous case, privacy, migration and concurrent claims |
| test_health.py, test_monuments.py, test_places.py | Database health, catalogue filters, pagination, IDs and slugs, geocoding and provider failures |
| test_ai_agent.py, test_providers.py, test_tts.py | Web and model fallback, validated sources, story caching and concurrent misses, audio handling |
| test_travel.py, test_search.py | Tour evidence and retries, retention, inventory validation, expiry and ownership, itinerary arithmetic and PDF export |
| test_frontend_contract.py, frontend_contract.mjs | Actual frontend JS wrappers, a local FastAPI server and real loopback MongoDB, with simulated upstream services |

For both integration tests, set MONGODB_TEST_URI to a disposable MongoDB server on 127.0.0.1 and FRONTEND_CONTRACT_DIR to a frontend checkout or snapshot containing src/api.js and src/auth.js. Then run **uv run pytest -q -ra**. See the [integration guide](../docs/frontend-contract.md) and [production QA findings](../docs/frontend-demo-qa.md).
