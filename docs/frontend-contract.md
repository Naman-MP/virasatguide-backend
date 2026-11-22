# Frontend contract, Google-linked credentials and migration

Status: merged into `main` and deployed to the existing production backend on 2026-11-22. Release commit `9ed1723` passed both Vercel build checks and live API smoke checks. No production secrets changed. The separate frontend repository was read only.

## Inspected frontend and initial gaps

Repository: `Rangesh64/virasat-frontend`, branch `feat/google-username-password`. The six source files below were fetched from that branch on 2026-11-22. The actual `api.js` and `auth.js` were executed against a local FastAPI server with real, isolated MongoDB; the other four files were reviewed for request construction and response-field use. This is API integration verification, not a rendered-browser UI or real human Google-consent test.

| Frontend file | Inspected blob SHA |
| --- | --- |
| `src/api.js` | `2240f9d74e32af838e4a8d3813671e8de72db1ff` |
| `src/auth.js` | `5fddd7de4f39c3292563250e9f865c255d2cd27e` |
| `src/session-ui.js` | `d33b555c5fe0acee1f490f2773a5c494a827a549` |
| `src/ai-tour-planner/script.js` | `f12b510a2fd3278c00a77f3cc902e4e279d1f3f3` |
| `src/ai-tour-guide/script.js` | `8712c4114ae6c124076007df36e89c7a9ef51eca` |
| `src/trip-planner/script.js` | `be3dc289f4e5e09785b6d237897852ab267a1ee6` |

The backend started on `main` with 196 passing tests and no uncommitted code changes. The unrelated untracked `prompts/` folder was preserved.

| Area | Before | This update |
| --- | --- | --- |
| Google Continue/callback | Already implemented with verified provider subject, PKCE/state/nonce and HttpOnly cookies | Preserved; also require a usable verified email |
| Google-linked password setup | Missing endpoint and public status fields | Added authenticated, atomic first-time setup on the same record |
| Login uniqueness | Case-sensitive account username only | Separate display/normalized login name, unique partial index and legacy backfill |
| Personal profile | Birth date required | Optional; omitted preserves saved DOB, null clears it |
| Tour questions | Owner checks, grounded evidence, last-three history, retry IDs already present | Preserved and regression-tested |
| Journey/planning inputs | Five stops, at most ten rooms | Six stops, up to twenty rooms, rooms still cannot exceed travelers |
| Autocomplete | Missing | Public official city lookup, at most six validated results |
| Plans, offers, PDF, story/audio | Already implemented | Reused; no second AI or persistence path |
| Validation errors | Could include submitted input | Omit input values, protecting passwords and private request data |

## Authentication contract

All existing routes remain: Google status/continue/login/signup/callback, password register/login, profile read/update, and logout.

`PUT /auth/credentials` requires the existing cookie or Bearer authentication and accepts only:

```json
{"username":"Traveler.One","password":"your-private-password"}
```

- Username: 3-32 ASCII letters, numbers, dots, underscores or hyphens. Whitespace is rejected. `google:` is reserved regardless of casing. Display casing is preserved; comparison is case-insensitive.
- Password: 8-128 characters, preserved exactly. Argon2 hashing runs in a threadpool. No plaintext password/hash is returned or logged.
- Only a currently authenticated Google-linked account with no password can configure credentials. Two simultaneous requests for the same account cannot overwrite the winner's password. A database unique index arbitrates simultaneous claims of the same normalized name, including local registration versus Google setup.
- Success returns the updated public profile, with private/no-store caching. It does not issue a separate account or replace the Google cookie. The stable account `username`, `_id`, Google subject, token version, personal onboarding status and saved-resource owners are unchanged.
- Errors: 401 unauthenticated; 403 non-Google or untrusted cookie Origin; 409 existing credentials/taken name/concurrent setup; 422 invalid body; 503 unavailable storage. Existing duplicate registration remains 400.

`GET /auth/me`, `PUT /auth/profile` and `PUT /auth/credentials` return:

```json
{
  "_id":"stable-account-id",
  "username":"google:verified-provider-subject",
  "login_username":"Traveler.One",
  "credentials_set":true,
  "full_name":null,
  "date_of_birth":null,
  "gender":"prefer_not_to_say",
  "onboarding_completed":false,
  "name":null,
  "email":null,
  "google_sub":"verified-provider-subject"
}
```

Values here illustrate field roles, not an existing user. Before Google credential setup, `login_username` is null and `credentials_set` is false. Legacy local password accounts derive these public values from their existing username even before migration. No hash, token, key or other stored private field is projected into this response.

`POST /auth/login` still accepts `application/x-www-form-urlencoded` username/password and returns exactly `{ "access_token": "...", "token_type": "bearer" }`. A new JWT is signed on each successful login, with a unique `jti`, the same stable internal `sub`, expiry and token version. Old valid JWTs remain compatible. Unknown usernames and wrong passwords both return `401 {"detail":"Incorrect username or password"}`. For legacy names differing only by case, the exact original spelling continues to work; an ambiguous alternate spelling fails closed.

Google identities are matched by the verified stable subject, never linked by matching email. Password and Google sign-in therefore resolve to the same record only after that signed-in Google user explicitly adds credentials. Google still works afterwards. Logout continues to revoke both cookie and Bearer sessions through the token version.

Cookie writes require an explicitly trusted Origin. CORS and cookie Origin checks share the same allowlist: configured frontend plus the existing production frontend; local development also permits `http://localhost:5173` and `http://127.0.0.1:5173`. No wildcard credentialed CORS is used. Redirects use the fixed backend settings, never a user-supplied redirect URL.

## Personal profile remains separate

A valid save can omit birth date:

```json
{"full_name":"Your submitted name","gender":"prefer_not_to_say"}
```

A valid full name (trimmed, 1-120 characters) completes personal onboarding. `gender` accepts male/female/non_binary/other/prefer_not_to_say; omitted or null means prefer_not_to_say. An omitted DOB keeps the stored date, explicit null clears it, and supplied values must be real YYYY-MM-DD dates from 1900-01-01 through today UTC. The frontend's blank DOB omission therefore works. Credential setup does not require these personal fields and does not change `onboarding_completed`.

## Login-name migration

The migration preserves all existing account keys, IDs, password hashes, JWT versions, collections and indexes. It adds/backfills only `login_username`, `login_username_normalized` and `credentials_set` for existing password accounts. Google users without passwords remain unconfigured.

Before rolling this update out to an existing database:

1. Take the normal database backup and pause backend writers using the old case-sensitive signup code.
2. Set `MONGODB_URI` privately and run a read-only audit with an explicit database name:

   ```sh
   uv run python scripts/migrate_login_names.py --database YOUR_DATABASE
   ```

3. If the audit reports collisions, record the count. The migration skips only those legacy login names; both accounts remain intact and can sign in with their original exact casing. An ambiguous alternate casing is rejected. Resolve these accounts with their owners later if case-insensitive login is required for them. Never rename the stable `username`, merge accounts, delete users or rewrite saved-resource owners. The script prints counts rather than account names, hashes or connection details.
4. Apply the migration for all noncolliding accounts:

   ```sh
   uv run python scripts/migrate_login_names.py --database YOUR_DATABASE --apply
   ```

5. Restart this version and verify Google and password login resolve the same profile and saved data.

Dry-run never writes. Apply first audits every existing login claim, then creates `unique_login_username_normalized`, a unique index with partial filter `{login_username_normalized: {$type: "string"}}`. This allows many Google users without chosen names. It backfills metadata without rewriting hashes/keys. If interrupted, rerun it; already-correct fields need no changes. Startup also runs this idempotent audit/backfill/index check; unresolved legacy collisions stay unindexed and require exact-case login. No existing indexes are dropped. The legacy lookup/reservation fallback prevents an unbackfilled local name from being claimed through new registration/setup. Do not resume older writers after migration.

The migration was tested against isolated local MongoDB, including partial uniqueness, idempotence, safe collision skip and simultaneous credential claims. The deployed backend includes the startup migration. The pre-deploy read-only audit found one collision group; those legacy records are skipped and retain exact-case login. A separate post-deploy record-count audit from this computer timed out during DNS resolution, although the live backend health check reported a connected database.

## Existing frontend travel and monument contracts

See [travel-api.md](travel-api.md) and [the full exact OpenAPI export](openapi.json) for every field and response.

- Public `GET /api/v1/monuments?page=1&page_size=50&search=...&city=...` returns `{items,total,page,page_size}`. Detail returns id/name/city/location_tag/cover_image/reference_text. The frontend's existing image normalization accepts `cover_image`.
- Authenticated story POST returns story/sources/research_status; audio POST returns audio/mpeg bytes, or a useful error. They share the existing story cache with tours.
- Session create/read/question routes and bodies are unchanged. Questions allow 1-1200 characters. Owner checks, evidence IDs, uncertainty responses, atomic version conflicts and last-three persisted history are retained. A same-ID/same-question retry reuses the saved answer while it is in those last three entries. Older IDs are not retained forever; a conflicting concurrent write returns 409 and the frontend retries.
- Plans accept the original minimal destination request and optional city/area/coordinates/budget/interests and route/date/preferences. Up to six intermediate stops are accepted. Rooms are 1-20 and cannot exceed traveler count. Destination/city/area max 120, days 1-7, travelers 1-20, interests up to eight of max 80, budget integer 0-100,000,000; integer counts reject booleans/strings. Supplied coordinates must be finite and in bounds and remain unverified search hints. Currency must be an ISO 4217 code.
- Plan responses retain plan_id/title/summary/created_at/request/days/estimated_total/budget_status/sources/warnings/tips plus existing proximity and offer-cost details. Each day retains day/title/stops/transport_cost/accommodation_cost/estimated_total; each stop retains place_name/area/description/time_of_day/duration_minutes/estimated_cost/evidence_ids.
- Day/trip totals and budget status are calculated by the server, not accepted from client/model totals. Live provider amounts stay separate from estimates. Offer owner, expiry, route, dates, party, rooms and currency checks remain intact.
- Saved GET returns the saved plan. PDF uses that same saved result and invokes neither AI, web search nor inventory providers. Both are private. Existing field names have not been removed.
- Source URLs accept only public HTTP(S) links without userinfo, secret query parameters or controls. Search skips invalid sources; citation IDs are checked against retrieved evidence. Evidence-linked generation still depends on the quality of the supplied catalogue/snippets; source-ID validation is not independent historical fact verification.

## Location suggestions

Public `GET /api/v1/travel/places/suggest?q=jaip` returns an array (which the current frontend directly accepts):

```json
[
  {
    "id":"geonames:1269515",
    "label":"Jaipur, Rajasthan, India",
    "name":"Jaipur",
    "country":"India",
    "country_code":"IN",
    "latitude":26.91962,
    "longitude":75.78781,
    "provider":"Open-Meteo / GeoNames",
    "source_url":"https://open-meteo.com/en/docs/geocoding-api"
  }
]
```

The example represents the provider's city record, not a route, live offer or precise monument location. The endpoint requests at most six official matches with a six-second timeout, validates labels/coordinates, rejects markup/controls and deduplicates provider IDs. Query max 120; one-character queries/no matches return an empty array. Malformed input is 422; invalid provider data 502; unavailable provider 503; timeout 504. Provider error bodies are not returned. No fictional fallback suggestions are inserted. Manual entry remains usable.

The integration uses the documented [Open-Meteo geocoding API](https://open-meteo.com/en/docs/geocoding-api), with location data attributed to GeoNames. Real searches for `jaip` and `Tokyo` both returned validated records during this task. The public API is for non-commercial use; review [Open-Meteo's terms](https://open-meteo.com/en/terms) and arrange permitted commercial access before a commercial launch. No geocoding key or additional secret was introduced for this demo. The existing frontend uses the returned labels; city coordinates do not establish itinerary distances or availability.

## Verification and repeatable local setup

Verified in this update:

- All **262 tests passed**, including the two opt-in local integration tests. Without their environment settings, the regular suite runs offline and skips those two checks.
- Signed test Google ID tokens cover issuer/audience/signature/nonce/state/email/subject verification. OAuth account creation, setup, Google re-login and same-record password login are covered.
- Real local MongoDB covers partial uniqueness, concurrent case-insensitive claims, same-account double setup, collision audit and idempotent backfill.
- Actual frontend API/auth wrappers cover guest discovery, Google cookie flow, setup fields, story/audio formats, sessions/questions/evidence/history/retries, capabilities, suggestions, unavailable inventory, six-stop itinerary inputs, computed totals, saved plan/PDF, optional profile DOB, username/password login, logout and cross-user privacy.
- The frontend wrapper integration simulates Google/AI/search/audio/geocoding services. It is a deterministic contract check, not a claim of live Google consent, live AI generation, real Fish Audio synthesis or licensed inventory access during that test. Existing provider, story and audio regression tests remain passing. Real geocoding requests were checked separately.
- Both backend and AI-agent packages built successfully. The exported OpenAPI matches the new route/schema definitions.

Run normal checks:

```sh
uv sync
uv run pytest -q
uv build --all-packages --out-dir /tmp/virasat-build
```

For real database and frontend wrapper checks, use a separate local MongoDB instance, Node, and a read-only checkout/snapshot of the named frontend branch:

```sh
mkdir -p /tmp/virasat-test-mongo
mongod --dbpath /tmp/virasat-test-mongo --bind_ip 127.0.0.1 --port 27028
# In another terminal, from the backend root:
MONGODB_TEST_URI=mongodb://127.0.0.1:27028 \
FRONTEND_CONTRACT_DIR=/absolute/path/to/virasat-frontend \
uv run pytest -q -s
```

The optional checks require an explicit 127.0.0.1 Mongo URI. They create uniquely named local test databases and remove only those test databases afterwards. They do not access existing Atlas accounts, stories, sessions or plans. Stop the separate MongoDB process after testing.

## Release and configuration status

Release `9ed1723` is on `main`; both Vercel projects reported successful builds. The production backend returned healthy MongoDB status, an OpenAPI schema matching the tested code, 20 monument records, six Jaipur suggestions, Google configuration ready, and 401 for unauthenticated credential setup. Production secret settings and Google Console settings were not changed, and the frontend repository was not edited. A real browser Google-consent and credential-setup walkthrough remains to be checked by a user.

Keep existing private backend configuration: MongoDB URI/database, JWT signing key, Google client credentials, exact backend callback, frontend return URL, AI settings and Fish Audio key. Local callback is `/auth/google/callback` on the local backend; production callback must remain `https://virasatguide-backend-epsu.vercel.app/auth/google/callback` and be registered identically with Google. Configure `FRONTEND_URL` for the intended dashboard; production CORS retains `https://virasat-frontend-nu.vercel.app`.

Gemini remains primary when configured, Ollama the fallback (or standalone local model). Production requires a valid Gemini key or reachable hosted Ollama; Vercel cannot reach a model on the owner's localhost. Existing Fish Audio configuration is unchanged. No licensed flight/train/bus/car/hotel provider account is available; those capabilities stay false and search returns honest unavailable results. AI readiness remains a bounded real probe, not key/route presence. No invented fares, hotel inventory or booking links were added.

## Changed files

- Authentication/data: `routes/auth.py`, `routes/google_oauth.py`, `data/credentials.py` (new), `data/profiles.py`, `data/security.py`, `data/database.py`, `main.py` under `src/virasatguide_backend/`.
- Contracts: `travel/places.py` (new), `travel/router.py`, `travel/schemas.py`, `travel/search_models.py`; shared safe source validation in `ai_agent/src/virasatguide_ai_agent/models.py`.
- Tools/tests: new `scripts/migrate_login_names.py`; existing smoke-script generated usernames shortened to the new limit; new credential/Mongo/autocomplete/frontend-wrapper tests and `tests/frontend_contract.mjs`; updated auth/Google/profile/travel/search regression fixtures and expectations.
- Documentation: root README, this guide, travel API, exact OpenAPI, and relevant folder READMEs.

No whole feature module was replaced, existing data was preserved, and no frontend file was changed.
