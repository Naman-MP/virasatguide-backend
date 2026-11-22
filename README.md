# Virasat Guide Backend

Start with [the demo walkthrough](docs/demo-walkthrough.md) for a plain explanation. Each source folder has a short README describing its files. The [travel API guide](docs/travel-api.md) contains the frontend integration details.

## Run

Requires Python 3.13+ and a working MongoDB Atlas connection.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

Fill in `.env`:

```dotenv
MONGODB_URI=your-mongodb-connection-string
MONGODB_DB=virasatguide
JWT_SECRET_KEY=your-random-secret-at-least-32-characters
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/auth/google/callback
FRONTEND_URL=http://127.0.0.1:5173/src/dashboard/index.html
```

Generate a JWT secret with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Allow your IP in Atlas network access. Keep `.env` private.

```sh
python -m uvicorn virasatguide_backend.main:app --reload --host 127.0.0.1 --port 8000
```

API docs: http://127.0.0.1:8000/docs

In a second terminal, open the frontend repo and run `npm run dev`. Open http://127.0.0.1:5173/src/landing-page/index.html. Use `127.0.0.1` for both servers. Restart the backend after editing `.env`.

## Google OAuth and JWT

The frontend should use `GET /auth/google/continue` for its single Google button. It signs in or creates a verified Google account. The legacy login/signup behavior described below is preserved for compatibility. After login, read `/auth/me` and submit actual onboarding information with `PUT /auth/profile`.

In Google Cloud Console, configure the consent screen, create an OAuth **Web application** client, and add this authorized redirect URI:

`http://127.0.0.1:8000/auth/google/callback`

Add your Google account as a test user if the app is in testing mode. Put the client ID and secret in the backend `.env`.

Implemented `/auth/google/login`, `/auth/google/signup`, and `/auth/google/callback` with FastAPI and Authlib. The flow validates Google's ID token, state and nonce, and uses PKCE. Sign-in only accepts an existing MongoDB user matched by Google account ID. If no account exists, the callback shows a sign-up page with a link to `/auth/google/signup`; that explicit flow creates the account after Google verification. Successful authentication sets a 30-minute JWT in an HttpOnly cookie and redirects to the frontend dashboard. `/auth/google/status` checks configuration. Google provider tokens and plaintext passwords are not stored. Chosen local passwords are stored only as hashes.

Password registration hashes passwords; `/auth/login` returns a bearer JWT. `/auth/me` accepts a valid bearer token or login cookie. JWTs require a valid signature, expiry, user and token version. `/auth/logout` revokes the user's existing tokens and clears the cookie. Cookie logout requires the configured frontend origin; CORS allows that origin. Accounts are not automatically linked by email.

## Test

```sh
uv sync
uv run pytest -q
```

Tests simulate Google and MongoDB. The real Google consent step is completed in your browser.

## Monument discovery

`GET /api/v1/monuments?search=fort&city=New%20Delhi&page=1&page_size=12` returns `items`, `total`, `page`, and `page_size`. Omit any filter to list all monuments. `search` matches the name, city, or location tag without case sensitivity; `city` is an exact city filter without case sensitivity. Results are ordered by numeric ID. Page numbers start at 1, and page size is limited to 50. The route is public.

`GET /api/v1/monuments/1` returns one monument with `id`, `name`, `city`, `location_tag`, `cover_image`, and `reference_text`. An unknown ID returns 404, an invalid ID returns 422, and a database outage returns 503. The route is public.

`GET /api/v1/monuments/{city-slug}/{monument-slug}` also returns one card by city and monument name. For example, `/api/v1/monuments/mumbai/gateway-of-india` and `/api/v1/monuments/new-delhi/india-gate` work with the current catalogue. The city and name must both match; `/api/v1/monuments/mumbai/india-gate` returns 404. Slugs are lowercase words joined by hyphens.

The frontend can debounce input (for example, wait 300 ms after the last keystroke) and cancel an older fetch with `AbortController`. Every request is independent, so the endpoint also works with a dedicated search service later.

## Monument story AI agent

The small LangGraph project is in `ai_agent/`. It validates the selected monument's database fields, searches DuckDuckGo (then Bing if needed), and asks Gemini (or the Ollama fallback) to write a spoken story. The graph has two steps: research and write. Web search snippets are supporting context, not verified historical facts.

Set `GEMINI_API_KEY` in the backend environment to use Gemini first. `GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`. Ollama is the fallback: set `OLLAMA_MODEL` to your installed tag (locally, `qwen3:8b`), `OLLAMA_BASE_URL` (locally, `http://127.0.0.1:11434`) and optionally `OLLAMA_THINK=false`. Without a Gemini key, Ollama is used directly. A protected hosted Ollama server can use `OLLAMA_API_KEY`. Install the workspace with `uv sync`; if you use pip, install both packages with `python -m pip install -e ./ai_agent` followed by `python -m pip install -e .`.

In `/docs`, use **Authorize** with a registered username and password before trying the story endpoint. For a command-line request, send the bearer token returned by `POST /auth/login` in the `Authorization: Bearer <token>` header. A request without a token returns 401.

An authenticated `POST /api/v1/monuments/7/story` looks up monument 7 in MongoDB and returns `monument_id`, `story`, `sources`, and `research_status`. The frontend should call this endpoint when the user opens a monument. It is separate from the existing monument card endpoint, so discovery remains fast. Generated stories are saved in MongoDB's `tour_stories` collection. Reopening the same monument with the same provider/model configuration and monument details returns the saved story; changing the provider/model configuration or monument details generates and saves a new one. The narration is cleaned into plain text before it is saved, so newline and tab escape sequences do not appear in the story. The plain story text is used by the audio endpoint below.

## Monument audio (Fish Audio)

Add `FISH_AUDIO_API_KEY` to the backend `.env`. Optionally set `FISH_AUDIO_VOICE_ID` to a Fish Audio voice model ID; if omitted, Fish Audio chooses its default voice. Restart the backend after changing `.env`.

First call authenticated `POST /api/v1/monuments/{id}/story` to save the story. Then call authenticated `POST /api/v1/monuments/{id}/audio` to receive an MP3 response (`audio/mpeg`). The audio route reads that saved story and sends its cleaned text to Fish Audio using the `s2.1-pro-free` model. A monument without a saved story returns 404. The MP3 is returned directly and is not stored in MongoDB, so another audio request generates it again.

For example, after signing in and generating the story:

```sh
curl -X POST http://127.0.0.1:8000/api/v1/monuments/3/audio \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -o monument-3.mp3
```

## Tour follow-ups, itinerary planning and PDF export

The authenticated `/api/v1/travel` router uses Gemini first, Ollama as the fallback, the saved monument story and web search. See [the travel API guide](docs/travel-api.md) for endpoint payloads, architecture, budgets, location behavior and PDF downloads.

## Production Google sign-in

Set these in the backend Vercel project's Production environment, then redeploy:

```dotenv
GOOGLE_REDIRECT_URI=https://virasatguide-backend-epsu.vercel.app/auth/google/callback
FRONTEND_URL=https://virasat-frontend-nu.vercel.app/src/dashboard/index.html
```

Register the identical HTTPS callback under the matching Google Cloud OAuth client's authorized redirect URIs. Local callbacks are rejected when `VERCEL_ENV=production`. The login cookie uses Secure, HttpOnly and SameSite=None for the separate HTTPS frontend and backend domains. Local HTTP development uses SameSite=Lax. Cookie-authenticated writes require the configured frontend Origin. Browser policies that block third-party cookies can still affect cross-site sign-in.

To run every feature against actual services, configure Atlas, local Ollama and Fish Audio, then run `uv run python scripts/smoke_backend.py`. It uses a disposable QA database, exercises account access, monument discovery, stories, MP3 output, tour questions, three-pair retention, trip plans, ownership and PDF export, then removes its test database. Artifacts are written to `/private/tmp/virasat-backend-qa` by default. `uv run pytest -q` runs the deterministic suite without external services.


## Production AI configuration

The backend source is deployed through GitHub/Vercel. Set `GEMINI_API_KEY` and `GEMINI_MODEL` in the backend Vercel project's Production environment, then redeploy. The primary Gemini path does not need Ollama hosting. For a working production fallback, also set `OLLAMA_BASE_URL` to a hosted HTTPS Ollama-compatible service, `OLLAMA_MODEL` to a model installed there and, if needed, `OLLAMA_API_KEY` for bearer authentication. Localhost on Vercel cannot access this Mac. Provider keys must never be frontend variables or committed files.

The same shared client validates output for stories, questions and plans. Provider timeouts, HTTP failures and invalid output trigger the configured fallback; provider refusals are not bypassed. The complete configuration and frontend integration contract are in [docs/travel-api.md](docs/travel-api.md). A successful `/health` checks MongoDB, not model readiness. After adding the real Gemini key, run an authenticated generation to verify the key/model/quota before declaring production AI ready.

## Current integration additions

Use `/auth/google/continue` for the single Google button, `/auth/me` to read onboarding status and `PUT /auth/profile` to save actual traveler information. The travel API also exposes transport/hotel search contracts and capability discovery. No licensed inventory provider is configured; those searches return unavailable with empty results. Extended plans accept route/dates/preferences and separate selected provider quotes from estimates. See [the exact API guide](docs/travel-api.md) and [OpenAPI contract](docs/openapi.json).


## Google-linked credentials and frontend integration

This version supports `Rangesh64/virasat-frontend`, branch `feat/google-username-password`, including `PUT /auth/credentials`. Google-linked users choose a login name/password on their existing account; stable JWT subjects and private resource ownership do not change. `/auth/me` includes `login_username` and `credentials_set`. Personal profile onboarding is independent and date of birth is optional. Existing discovery, stories/audio, Q&A, plans, saved plan reads and PDF routes remain.

Before starting against an existing database, stop its backend writers and audit the login migration:

```sh
uv sync
uv run python scripts/migrate_login_names.py --database YOUR_DATABASE
# After reviewing the audit and taking a normal database backup:
uv run python scripts/migrate_login_names.py --database YOUR_DATABASE --apply
uv run uvicorn virasatguide_backend.main:app --host 127.0.0.1 --port 8000
```

Use your actual database name, and keep `MONGODB_URI` private in the backend environment. Dry-run does not write; apply backfills only login metadata and adds a partial unique index. Startup repeats this idempotently; colliding legacy names remain unchanged and require exact-case login. Never change the stable `username` account key to resolve a chosen login name. See [the exact contract, migration and verification guide](docs/frontend-contract.md) for collision handling, frontend checks, optional local Mongo tests and configuration. Deployed to the existing production backend on 2026-11-22. No production secrets were changed.

## Repository guides

| Area | Guide |
| --- | --- |
| Request lifecycle | [Backend package](src/virasatguide_backend/README.md) |
| Authentication, health, monuments and audio | [Routes](src/virasatguide_backend/routes/README.md) |
| MongoDB collections, indexes and account identity | [Data](src/virasatguide_backend/data/README.md) |
| Shared story generation and persistence | [Services](src/virasatguide_backend/services/README.md) |
| Tours, itinerary, travel search and PDF | [Travel](src/virasatguide_backend/travel/README.md) |
| Research, writing and model fallback | [AI agent](ai_agent/README.md) |
| API contracts, demo QA and release notes | [Documentation](docs/README.md) |
| Regression, concurrency and frontend wrapper tests | [Tests](tests/README.md) |
| Migration and real-service checks | [Scripts](scripts/README.md) |
| Editor settings | [VS Code](.vscode/README.md) |
