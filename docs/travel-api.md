# Travel API

Travel endpoints run under `/api/v1/travel` and require the same login used for story generation, except public `GET /capabilities`. Use the bearer token returned by `POST /auth/login`, or the Google login cookie from the configured frontend. Local Swagger is at http://127.0.0.1:8000/docs.

## Authentication quick index

- `GET /auth/google/continue`: the new single Google sign-in/signup entry point.
- `GET /auth/me`: public account fields and onboarding status.
- `PUT /auth/profile`: authenticated profile onboarding.
- Existing password and Google login/signup endpoints remain available.

## Architecture

```text
FastAPI travel router
  |
  +-- Tour sessions --> existing monument story service --> MongoDB catalogue + cached story
  |      |
  |      +-- question --> retrieve relevant passages --> Gemini / Ollama JSON answer --> session history
  |
  +-- Trip plans --> catalogue + DuckDuckGo/Bing snippets --> Gemini / Ollama JSON itinerary
  |      |
  |      +-- validate days, schedule, citations --> calculate costs --> MongoDB trip_plans
  |
  +-- PDF download --> read the saved plan --> ReportLab --> application/pdf
```

Files in `src/virasatguide_backend/travel/`:

- `router.py` exposes the endpoints and handles ownership, storage and concurrent questions.
- `schemas.py` defines and validates every input, model response and API response.
- `llm.py` keeps the `generate_json(schema, instruction, context)` contract. It delegates to `ai_agent/src/virasatguide_ai_agent/providers.py`, which calls Gemini first and Ollama as the fallback. Both results must pass the same Pydantic validation. Stories use that same provider module.
- `retrieval.py` splits the monument reference, generated story and research snippets into overlapping passages. A simple count of shared words selects four relevant passages for each question. It uses recent questions to resolve follow-ups such as "Why was it built?".
- `planner.py` researches the destination, generates itinerary cards, validates references, place names and schedules, allows one correction attempt, and computes totals.
- `pdf.py` lays out the saved itinerary with a budget summary, daily cards, practical notes, source references and page numbers.

The existing story route and tour sessions now share `services/stories.py`. The story route retains its request and response format.

## Setup

Install dependencies with `uv sync`. The same settings apply to stories, tour questions and trip plans:

```dotenv
# Primary provider: add your private key when it is available.
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash-lite

# Fallback provider: this setup works on the development Mac.
OLLAMA_MODEL=qwen3:8b
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_THINK=false
# Optional bearer token for a protected hosted Ollama server.
OLLAMA_API_KEY=
```

The default Gemini model is configurable. See [Google's model documentation](https://aistudio.google.com/docs/models/gemini-3.5-flash-lite) and [REST API reference](https://aistudio.google.com/api-reference/generate-content/generate-content). The implementation uses ordinary HTTP calls, not an extra SDK or agent loop.

### Provider behavior

1. If `GEMINI_API_KEY` is present, try Gemini first.
2. If Gemini times out, rejects the call (including quota/key errors), returns empty/incomplete output, or returns JSON that fails schema validation, try configured Ollama once.
3. If the Gemini key is absent, use configured Ollama directly. This supports local development while waiting for the key.
4. Provider refusals are reported without trying to bypass them through the fallback. Source and schedule validation still run after a valid JSON response; the itinerary may make its existing single correction attempt.
5. If no configured provider succeeds, return a clear JSON error. Do not save a failed generation as a completed story, answer or plan. Logs and API errors exclude keys, prompts and raw upstream response bodies.

Gemini requests time out after 45 seconds; Ollama requests after 180 seconds. Fallback and the itinerary correction attempt can extend the total request duration. Keep initial trips short. Cached stories and saved plans do not call an LLM again.

### Local and production setup

Restart the backend after changing environment settings. For the existing automatic macOS service:

```sh
launchctl kickstart -k gui/$(id -u)/com.virasatguide.backend
```

In the backend Vercel project's **Production** environment, set `GEMINI_API_KEY` and `GEMINI_MODEL`, then redeploy. Keep provider keys on the backend; never put them in frontend `VITE_*` variables or GitHub. Google OAuth's client ID/secret do not replace a Gemini API key.

Gemini can serve production requests without a hosted Ollama server. To make the **fallback** work from Vercel too, provide a hosted Ollama service, set `OLLAMA_BASE_URL` to its HTTPS address, set `OLLAMA_MODEL` to a model installed there, and set `OLLAMA_API_KEY` if that service uses bearer authentication. The service must expose the Ollama-compatible `/api/chat` endpoint. Do not use the Mac's localhost URL on Vercel; the code rejects it with an actionable error. This project does not provision hosting or expose the Mac's Ollama server publicly.

Deployment and configuration are separate: publishing this code does not create a Gemini key or a hosted Ollama server. Test a real authenticated generation after adding these credentials. `/health` confirms database connectivity, not LLM connectivity.

## Endpoints

| Method | Path | Result |
| --- | --- | --- |
| GET | `/capabilities` | Public operational readiness flags |
| POST | `/transport/search` | Authenticated transport search; unavailable until a licensed adapter is ready |
| POST | `/hotels/search` | Authenticated hotel comparison; unavailable until a licensed adapter is ready |
| POST | `/tour-sessions` | Start a private session for a monument, generating its story if needed |
| GET | `/tour-sessions/{session_id}` | Read the story and the last 3 question/answer pairs |
| POST | `/tour-sessions/{session_id}/questions` | Ask a grounded follow-up and save the answer |
| POST | `/plans` | Generate and save a trip plan |
| GET | `/plans/{plan_id}` | Fetch the exact saved itinerary cards |
| GET | `/plans/{plan_id}/pdf` | Download the saved plan as PDF |

All paths in the table are relative to `/api/v1/travel`. IDs are UUID strings, except numeric catalogue `monument_id`. Unknown resources and another user's resources both return 404.

### 1. Start a tour

Send this to `POST /api/v1/travel/tour-sessions`:

```json
{"monument_id": 3}
```

The response contains `session_id`, `monument_id`, `monument_name`, `story`, `created_at`, and `history`. MongoDB stores a snapshot of the story and its supporting passages in `tour_sessions`. Later changes to a monument do not silently change an existing tour's context.

### 2. Ask a follow-up

Send this to `POST /api/v1/travel/tour-sessions/{session_id}/questions`:

```json
{
  "question": "Who built this monument and why?",
  "request_id": "8c8fa0c8-52ad-4f49-9119-96b3934438e8"
}
```

The response contains `request_id`, `question`, `answer`, `supported`, `evidence`, and `created_at`. Each evidence item has an `id`, `title`, `text`, `kind`, and optional `url`.

`request_id` is optional. Generate a new UUID for a new question and reuse it for retries. Repeating an ID with the same question returns its saved answer while that question is within the last 3 history entries. Older question/answer pairs are removed from MongoDB atomically when a new answer is saved. Reusing the ID with a different question returns 409. Simultaneous questions use a version check, so one may return 409 and can be retried without losing the other answer. The frontend should send one question at a time per session.

When the model cannot support an answer from the retrieved context, the response sets `supported=false`, returns a short refusal, and includes no citations. Citation IDs are checked against the retrieved passages. This improves grounding, but a generated story and search snippets are not independently verified historical sources. No new web request is made for each question.

### 3. Generate a trip plan

The smallest valid request to `POST /api/v1/travel/plans` is:

```json
{"destination": "Agra"}
```

A fuller request is:

```json
{
  "destination": "Agra",
  "city": "Agra",
  "area": "Taj Ganj",
  "location": {"latitude": 27.1751, "longitude": 78.0421},
  "days": 2,
  "travelers": 2,
  "budget": 6000,
  "currency": "INR",
  "interests": ["heritage", "local food"]
}
```

Defaults are 1 day, 1 traveler and INR. Budget is optional, expressed in whole currency units for the entire trip and all travelers. A budget of zero is valid. The API accepts 1-7 days and 1-20 travelers. To change budget or preferences, create another plan with the revised request.

Each returned day contains a title, stop cards, transport cost, accommodation cost and a server-calculated total. Every stop includes its name, area, morning/afternoon/evening slot, duration, estimated cost, description and evidence IDs. The backend adds those amounts to compute `estimated_total` and sets `budget_status` to `not_set`, `within_budget` or `over_budget`. It reports an over-budget result honestly.

Destination, city, area and optional coordinates guide web search. The current catalogue has no reliable coordinates, so exact distance filtering and route distances are not computed. `proximity_status` reports `destination_search`, `area_search` or `coordinates_unverified`. Prices, opening hours, availability and proximity require confirmation. Search failure falls back to catalogue entries for the city; if neither source is available, the API returns 503.

### 4. Download the PDF

Call `GET /api/v1/travel/plans/{plan_id}/pdf` with the same authentication. It returns `application/pdf` with an attachment filename. In a browser, fetch it with the bearer token or credentials, check the HTTP status and content type, then turn the successful response into a Blob for download. Error responses are JSON and should be displayed as errors.

The PDF uses the saved itinerary and makes no new model or search calls. It uses embedded Latin fonts, currency codes such as INR, escaped text, wrapping cards and automatic page breaks. The current PDF design targets English and Latin-script content; complex Indic-script shaping needs a dedicated font/shaping extension.

## Errors

- 401 means login is missing, expired or invalid.
- 404 means the monument, session or saved plan was not found for this user.
- 409 means concurrent session changes or a reused question ID.
- 422 means invalid input, including blank fields, invalid UUIDs, coordinates or negative budgets.
- 502 means invalid model output, a provider refusal, invented evidence IDs, missing days or an invalid schedule.
- 503 means MongoDB, configured AI providers or destination evidence is unavailable.
- 504 means no provider succeeded and the final provider call timed out.

## Storage and limits

`tour_stories` remains the shared monument story cache. Its key includes monument context and the configured Gemini/Ollama model policy, so enabling Gemini or changing models creates a fresh cache entry. Stored `generation_config` describes the configured policy, not a claim about which provider produced a fallback story. No API keys are stored. Ollama-only cache keys remain compatible while the Gemini key is absent, including audio lookup. Existing cache entries are retained; old entries are not automatically deleted. `tour_sessions` stores private context snapshots and the last 3 Q&A pairs. `trip_plans` stores each private plan, its inputs, sources, warnings and calculated totals. MongoDB's native `_id` index handles session and plan UUID lookups. Failed model calls are not saved as successful answers or plans. Sessions and plans currently have no automatic expiry.

## Verification

```sh
uv run pytest -q
```

The suite exercises authentication, ownership, retrieval, retry IDs, concurrent writes, validation, search fallback, budget arithmetic, model failures, binary PDF responses and long multipage output.


For the frontend, keep the returned `session_id` and `plan_id` to load those resources later. Requests and response fields are unchanged by provider selection. Use `Authorization: Bearer <token>` for password login; cookie-based Google login needs `credentials: "include"` on fetch calls. Check response status before reading JSON or downloading a PDF. Frontend code never calls Gemini or Ollama directly.


## Unified Google authentication and traveler onboarding

`GET /auth/google/continue` starts the single **Continue with Google** flow. Navigate the browser to this backend URL; do not fetch the Google authorization redirect with JavaScript. Google state, nonce, signatures, issuer, audience and PKCE checks remain handled by Authlib. A verified identity signs in an existing account or creates a new Google account safely. Password accounts are never linked by matching email. Existing `/auth/google/login` (existing users only), `/signup`, `/callback`, password registration/login and logout remain available.

After the callback returns to the configured frontend, call `GET /auth/me` with `credentials: "include"`. If `onboarding_completed` is false, collect the traveler's actual information and call authenticated `PUT /auth/profile`:

```json
{"full_name":"Your submitted name","date_of_birth":"2000-01-01","gender":"prefer_not_to_say"}
```

These values illustrate the schema only. The backend does not fill in a sample person's identity. Profile fields are stored in MongoDB, not localStorage. `full_name` is trimmed and must have 1-120 characters. Birth dates require exact YYYY-MM-DD and a valid calendar date between 1900-01-01 and today in UTC. Gender accepts `male`, `female`, `non_binary`, `other`, and `prefer_not_to_say`; omitted or null gender means `prefer_not_to_say`. All three valid fields complete onboarding; the client cannot submit `onboarding_completed`, a username or another user's ID.

Both profile endpoints return this public shape (nullable fields use JSON null):

```json
{
  "_id":"account-id",
  "username":"account-username",
  "full_name":null,
  "date_of_birth":null,
  "gender":"prefer_not_to_say",
  "onboarding_completed":false,
  "name":null,
  "email":null,
  "google_sub":null
}
```

`name`, email and stable Google subject are included when available. Legacy `_id`, username, name and email fields remain. Password hashes, provider credentials and authentication tokens are never included in profiles. Writes use the authenticated account ID and cookie writes require the configured frontend Origin. Production CORS now also permits PUT. Responses use private/no-store caching.

## Transport search

Authenticated `POST /api/v1/travel/transport/search` accepts:

```json
{
  "origin":{"label":"Bengaluru, India"},
  "destination":{"label":"Jaipur, India"},
  "stops":[{"label":"Hyderabad, India"}],
  "departure_date":"2026-12-10",
  "return_date":"2026-12-15",
  "travelers":2,
  "modes":["car","train","flight","bus"],
  "currency":"INR"
}
```

Location labels support worldwide manual entry and have 1-200 characters. Stops are ordered and limited to five. Adjacent duplicate locations are rejected; a route that returns to its origin through other stops is allowed. Dates must use YYYY-MM-DD; departure must be today (UTC) or later. Return is nullable for one-way travel and cannot precede departure. Travelers must be an integer 1-20. Modes must be a nonempty distinct subset of car/train/flight/bus. Car means supported car-rental inventory; this is not an invented fuel-cost or driving-time quote. Currency must be an uppercase ISO 4217 code.

Exact top-level response fields: `search_id` (UUID), `status`, `searched_at` (timezone-aware ISO timestamp), `currency`, `results`, `unavailable_modes`, `warnings`.

Each result has `id` (backend-generated UUID), `mode`, `provider`, `service_name`, nullable timezone-aware `departure_at` and `arrival_at`, nullable positive `duration_minutes`, nullable `price`, nullable `booking_url`, and timezone-aware `retrieved_at`. A price has `{amount: number, currency: string, type: "live" | "estimate"}`. Amounts cover **all travelers and every requested leg, including a return when requested**. The adapter must supply a complete supported journey; the backend never stitches unrelated tickets or claims an unsupported multi-stop route exists.

`complete` means the requested modes have usable complete provider responses. `partial` indicates missing modes, provider failures, estimated/missing prices or missing links. `unavailable` means no valid offers. Confirmed live totals sort before estimates and unknown prices; within those groups amounts sort ascending. A live quote describes the provider response at `retrieved_at`, not a guarantee of current availability or final checkout charges.

Current production inventory response is intentionally unavailable while there are no licensed provider accounts/adapters:

```json
{
  "search_id":"generated-uuid",
  "status":"unavailable",
  "searched_at":"server-generated-timestamp",
  "currency":"INR",
  "results":[],
  "unavailable_modes":["car","train","flight","bus"],
  "warnings":["No verified transport offers are available for this request. No prices or booking links were invented."]
}
```

The IDs/timestamps above describe field roles, not real offers.

## Hotel search

Authenticated `POST /api/v1/travel/hotels/search` accepts:

```json
{
  "destination":"Jaipur, India",
  "check_in":"2026-12-10",
  "check_out":"2026-12-15",
  "travelers":2,
  "rooms":1,
  "star_ratings":[2,3,4,5],
  "currency":"INR"
}
```

Destination is 1-200 characters. Dates are exact YYYY-MM-DD, check-in is today (UTC) or later, and checkout is later by 1-30 nights. Travelers are 1-20; rooms are 1-10 and cannot exceed traveler count. Star filters are distinct integers 1-5; an empty list means no star filter. Unknown star ratings cannot satisfy an explicit star filter.

Top-level response: `search_id`, `status`, `searched_at`, `results`, `warnings`. Hotel results contain `id`, `hotel_name`, `location`, nullable `star_rating`, `provider`, nullable `nightly_price`, nullable `total_price`, nullable `booking_url`, and `retrieved_at`. Price objects use the same amount/currency/type fields. Nightly prices cover all requested rooms; total prices cover the complete stay and all rooms/travelers. The backend does not multiply a nightly rate and label the result a live total. Missing totals remain null. Statuses have the same complete/partial/unavailable meaning. There is no booking, ticket sale or payment endpoint: Virasat only redirects to a verified public provider page.

## Offer safety, ownership and provider setup

No transport/hotel vendor is currently integrated or registered. Search adapter tests use fixtures only; those fixtures never appear in the running application. The owner confirmed that no provider account is available yet. Both search routes are real, validated endpoints that return honest unavailable results in this deployment.

`search_providers.py` defines the `TransportProvider` and `HotelProvider` interfaces and empty registries. To enable inventory, select a licensed provider, obtain credentials and inventory permissions/commercial access, implement its official API adapter, run authenticated real searches, then register that tested adapter. There are **no provider-specific credential variables or enable flags implemented yet**; merely adding an arbitrary key cannot enable a capability. The adapter's `is_ready()` must authenticate/check inventory access, and `supports()` must enforce its countries, dates, stops, traveler and room limitations. Services requiring passenger ages or other unavailable request information must not assume values and fabricate a quote.

Booking URLs must be public HTTPS links returned by the provider, with no userinfo, fragments, control characters or credential query fields. Their host must match the adapter's verified booking-host allowlist. Raw upstream errors, credentials and private response bodies are not returned or logged. Invalid or stale offers are omitted and warnings indicate incomplete results.

Valid offers are saved in MongoDB `travel_offers` with their owner, original search request and expiry. They expire 15 minutes after the quote retrieval/search snapshot, whichever is earlier. A TTL index removes old records; selection queries enforce expiry immediately even if MongoDB cleanup is delayed. Another user's IDs, unknown IDs and expired IDs return the same 404. Mismatched route/dates/party/currency/room preferences return 409 and require a new search.

## Extended planning (backward compatible)

`POST /api/v1/travel/plans` still accepts the original minimal request and all old fields. New optional fields:

| Field | Type/default |
| --- | --- |
| origin | `{label: string}` or null |
| stops | ordered list of places, default `[]`, maximum 5 |
| departure_date | YYYY-MM-DD or null |
| return_date | YYYY-MM-DD or null; requires departure, cannot precede it |
| travel_mode | car/train/flight/bus or null |
| hotel_preferences | `{star_ratings: [], rooms: 1}` or null |
| selected_transport_id | UUID or null; requires origin and departure |
| selected_hotel_id | UUID or null; requires departure |

A request may include dates/preferences without selecting offers. For creation, departure cannot be in the past. Saved plans remain readable after their dates pass. `days` remains an independent 1-7 itinerary-card count, not automatically rewritten from the date interval. Route stops guide catalogue/web research and model context; this is not verified map routing or distance optimization. Countries and location identities are not inferred from unverified coordinates or labels.

When enough inputs exist, available provider results are included in model context. Without configured providers the itinerary can still use research and clearly labeled estimates. It does not create fake transport/hotel search offers. Intermediate destinations are researched as well. Selected offers must belong to the same user, be fresh, and match the requested journey/stay. Hotel stays must fit within the supplied travel dates (or departure plus itinerary days when no return is given).

The existing day cards and fields are retained. Added plan response fields are nullable `selected_offers` and `cost_summary`. `selected_offers` contains `transport`, `hotel`, `hotel_rooms`, `hotel_check_in`, and `hotel_check_out`. These are saved snapshots, so later GET/PDF calls do not query providers again. Old stored plans load with these fields null.

`cost_summary` contains `currency`, `itinerary_estimate`, `confirmed_total`, `selected_estimate_total`, `trip_total`, and `costs_complete`. Only provider prices explicitly marked live enter `confirmed_total`. Provider estimates enter `selected_estimate_total`; model day costs remain estimates. Selected transport covers the intercity journey separately from daily/local transport estimates. A selected hotel stay total replaces the model's accommodation estimate **only for the nights covered by that stay**, preventing double counting.

`estimated_total` remains the budget-comparison total; for selected offers it equals the itinerary estimate plus selected quote amounts, and can include decimal units. Legacy requests still produce integer whole-unit estimates. `budget_status` remains not_set/within_budget/over_budget. If selected prices or requested journey/hotel selections are missing, `costs_complete=false`: the frontend must present the budget as incomplete, not an all-in guaranteed price. Missing journey fares are not silently treated as verified zero costs. All quotes must be rechecked with the provider.

The PDF adds route, departure/return dates, transport/hotel preferences, selected provider snapshots, quote timestamps, separate confirmed/estimated cost summaries, and cautions when those fields exist. Legacy plans retain the existing layout and endpoints:

- `GET /api/v1/travel/plans/{plan_id}`
- `GET /api/v1/travel/plans/{plan_id}/pdf`

## Capability discovery and deployment readiness

Public `GET /api/v1/travel/capabilities` returns exactly four booleans: `transport_search`, `hotel_search`, `extended_planning`, and `profile_onboarding`.

The endpoint first checks MongoDB. Transport/hotel flags require at least one registered adapter to pass an authenticated readiness check; there are currently none, so both are false. `extended_planning` requires a successful small model-generation probe, not just an environment key. The AI probe is bounded to six seconds and cached for one minute per worker. A false result is conservative: a slow/loading/unreachable model is not advertised as operational. `profile_onboarding` is true when database-backed onboarding can operate. Readiness is a snapshot, not a guarantee that later providers will not time out.

Production target: [existing backend](https://virasatguide-backend-epsu.vercel.app). Frontend Origin: `https://virasat-frontend-nu.vercel.app`. Vercel deploys this repository using the existing pyproject FastAPI entrypoint. No frontend repository files are changed. The final release report records the verified deployed commit and observed production flags; `/capabilities` is authoritative for current readiness.

Required owner configuration remains MongoDB URI/database, JWT signing secret, Google client ID/secret, production Google callback and frontend URL. Configure `GEMINI_API_KEY` / optional `GEMINI_MODEL` for cloud AI, or a reachable hosted `OLLAMA_BASE_URL`, installed `OLLAMA_MODEL`, and optional bearer `OLLAMA_API_KEY` for production fallback. A local Mac URL does not work from Vercel. Fish Audio requires `FISH_AUDIO_API_KEY`; a configured voice ID remains optional. No provider key belongs in frontend VITE variables. There is no configured live travel inventory provider until an account and tested adapter are supplied.

Optional destination autocomplete is **not implemented** in this release. The frontend should retain manual worldwide location entry.

## Exact schemas and integration reference

[docs/openapi.json](openapi.json) is the exported complete OpenAPI contract, including every Pydantic request/response field, nullability, defaults, field constraints expressible in OpenAPI, authentication requirements and preserved endpoint. Cross-field and dynamic date rules described above are also enforced at runtime. The deployed `/openapi.json` and `/docs` expose the running release. Use this artifact to generate frontend types rather than guessing from illustrative offer examples.

New errors: profile/search input failures return 422; unknown/private/expired selected offers return 404; incompatible selected offers return 409. Provider search failures normally return 200 with an honest unavailable/partial search result. Database failures return 503. Existing model error statuses remain unchanged.
