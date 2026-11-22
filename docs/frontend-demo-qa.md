# Frontend demo QA handoff

This report records the November 22 website and backend checks and the fixes prepared for the follow-up release. The [API contract](frontend-contract.md) describes the integration, and [OpenAPI](openapi.json) defines the exact request and response shapes.

**Demo blocker observed on the public site.** The production backend had no usable Gemini or hosted Ollama configuration. Fresh story generation, starting a tour, and itinerary generation returned 503. Taj Mahal audio returned 404 because production had no saved story for its current configuration. The Atlas connection was healthy. The same story, audio, tour and planning flows worked with the local model and disposable Atlas data, although the dated itinerary needed a retry.

## Website and production API checks

| Area | What we exercised | Result |
| --- | --- | --- |
| Navigation and discovery | Landing page, dashboard, monument list, monument guide and trip planner | All five pages returned HTML. The browser loaded all 20 cards, showed one result for Taj Mahal, handled an empty search, and opened the detail from the card. |
| Tour and trip sign-in gates | Start protected actions as a guest | The live trip form displayed a sign-in prompt. Protected credential setup and other authenticated endpoints rejected unauthenticated requests. |
| Location input | Type Jaip and select Jaipur, Rajasthan, India | Autocomplete displayed results in the live browser and applied the selected label. A short query returned a successful empty response on the API. |
| Google OAuth | Live browser sign-in, dashboard, first-time credential prompt, and dismiss | Google returned the browser to the signed-in dashboard. The setup prompt appeared. Dismissing it left the dashboard accessible. Saving a password on the real account was skipped. |
| OAuth and cookie contracts | Continue, legacy login and signup redirects, callback URL, PKCE, cookie flags and cancellation | Verified Google's configured callback and secure OAuth cookie. Cancellation returned to the frontend with an auth error. Linked credential setup and stable ownership after login were exercised with disposable test accounts. |
| Password login and profile | Register, reject a case-insensitive duplicate and wrong password, log in twice, save profile without birth date | Live API checks passed. Successive logins issued fresh tokens. A local-only account received 403 on Google-only credential setup. Atlas contained the saved profile. Logout invalidated both issued tokens. |
| Browser origin and request bounds | Credential/profile/plan/story preflights, invalid page, missing monument and private resources, six versus seven stops | Production CORS allowed credentialed requests from the deployed frontend. Invalid page and seven stops returned 422. Six stops were accepted. Missing records returned 404. |
| Travel listings | Signed-in transport and hotel search and public capabilities | Both searches returned unavailable with empty results. Both inventory flags were false. Profile onboarding was true and extended planning was false at the time of the production check. |
| AI routes | Signed-in creation requests on Vercel | Fresh story, tour creation and planning each returned 503. Taj Mahal audio returned 404 because the required story was absent. |

## Database and concurrency checks

The configured URI connected directly to Atlas. The cluster reported a writable primary and three replica-set members. The catalogue contained 20 records. The stable username, normalized login name, monument ID, story cache key and travel offer expiry indexes were present. The login migration audit showed zero pending backfills and one existing legacy case collision group. Those legacy accounts retain their original case. The production test account and disposable Atlas databases were removed after use.

| Concurrent test | Result |
| --- | --- |
| Eight first-time callbacks for the same simulated Google subject against real Atlas | Returned one stable user record. The real Google browser sign-in was verified separately. |
| Six first-time credential attempts on one user | One succeeded. The five competing requests returned 409. |
| Six registrations of different cases of the same username | One record was saved. The five duplicate registrations were rejected. |
| Credential setup competing with standalone registration | The local MongoDB integration test confirmed a single winner in the shared normalized namespace. |
| Four simultaneous questions with the same request ID | One answer was saved. Repeating the same ID and text returned the same answer. |
| Four simultaneous questions with distinct IDs | The conflicting saves returned 409. Retries succeeded, leaving three distinct recent answers in the record. Reusing a retained ID with different text returned 409. |
| Four simultaneous requests for an uncached story | The first run returned four different texts while MongoDB saved one. The follow-up fix returned the one committed text to all four callers on the Atlas rerun. Simultaneous misses can still start multiple model calls. |
| Three legacy usernames differing only by capitalization | The previous two-row lookup could miss the third spelling. The follow-up fix checks the exact stable name when the case-insensitive result is ambiguous. All original spellings passed, and an alternate ambiguous spelling was rejected. |

## Real services and regression suite

- **263 backend tests passed**, including the local MongoDB race tests and the exact API and auth JavaScript served by the live frontend. The wrapper tests simulated upstream providers and verified linked credentials, saved session and plan ownership, retry behavior, and PDF binary responses. Both Python packages built.
- **Local Ollama** generated and reused a saved story, answered tour follow-ups, enforced history retention, and generated a normal itinerary with server-calculated totals. The dated itinerary returned 502 after failing draft validation on the first smoke run. A focused retry passed with the published validators, saved the plan, rejected cross-account reads, and produced a two-page PDF with the route and date. The first failure is evidence of intermittent model-output validation.
- **Fish Audio** returned a real 44.1 kHz MP3 lasting about 86 seconds in the isolated run. The normal itinerary PDF had two readable pages. The focused retry also verified the dated PDF.
- **Web research** returned four HTTPS results from Bing in a targeted probe. DuckDuckGo failed that probe. The basic itinerary's accepted stops cited catalogue sources.
- **Inventory** is intentionally empty on the live deployment until licensed adapters and credentials are configured. The suite tests offer sorting, incomplete results, expiration and ownership with fixtures.

## Frontend demo checklist

1. Demonstrate guest navigation, monument search, suggestions, and Google sign-in on the public website. Use a disposable account to verify the full credential-setup flow in a browser. Send cookie credentials with cross-origin authenticated requests.
2. Treat production AI readiness as unavailable until a valid Gemini key or hosted Ollama is configured and an authenticated production request actually generates a story and itinerary. Surface 503 with a clear service message. If audio returns 404 for a missing story, offer story generation when the model is available.
3. Use the travel capability flags to explain when comparison or extended planning is unavailable. Keep unconfirmed prices separate from quotes. Offer a retry for a 502 draft-validation failure.
4. Disable duplicate clicks while a story or question is in flight. Preserve the request ID when retrying a tour question after a 409. Use a fresh ID for different text. Display the latest server response and the retained three exchanges.
5. Preserve the stable account identity when a user adds a login name. Display the chosen login name and keep profile onboarding separate from credential setup.

Production confirmation should cover a new story, MP3 playback, tour Q&A, a new itinerary, reopening the saved plan, and downloading its PDF once a usable model is configured.
