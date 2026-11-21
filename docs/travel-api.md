# Travel API

These endpoints run under `/api/v1/travel` and require the same login used for story generation. Use the bearer token returned by `POST /auth/login`, or the Google login cookie from the configured frontend. Local Swagger is at http://127.0.0.1:8000/docs.

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
- `retrieval.py` splits the monument reference, generated story and research snippets into overlapping passages. A keyword-based score selects four relevant passages for each question. It uses recent questions to resolve follow-ups such as "Why was it built?".
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
