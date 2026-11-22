# Implemented backend features and final verification

Verification date: 2026-11-22. Tested application snapshot: `e74339fa7a35e109c8027cebed52aac40cea77a4`. This note records existing implemented functionality and the latest verification; it does not claim new live vendor integrations.

The code has been simplified for readability, and small additional features have been implemented alongside the existing backend. The feature list below describes the current backend.

## Implemented features

1. Monument discovery with text search, city filters, stable pagination, numeric IDs and readable city/name detail URLs.
2. Password registration/login, password hashing, signed JWTs, Bearer/cookie authentication, public profiles and logout/token revocation.
3. Secure Google OAuth login/signup plus the unified Continue with Google sign-in-or-create flow; legacy auth routes remain available.
4. MongoDB-backed traveler onboarding with submitted full name, validated birth date, optional gender disclosure and onboarding status.
5. A two-step LangGraph story agent using monument database context, DuckDuckGo/Bing research, validated models, cleaned narration and cached stories.
6. Gemini-first model calls with Ollama fallback, bounded timeouts, structured-output validation and sanitized provider errors.
7. Fish Audio S2.1 Pro Free MP3 narration from saved monument stories.
8. Private tour sessions with evidence-based follow-up questions, keyword passage retrieval, citation checks and unsupported-answer handling.
9. Only the latest three Q&A pairs retained per session, with retry IDs, ownership checks and protection against concurrent overwrites.
10. AI itinerary cards with destination, city/area/location hints, interests, days, travelers, flexible budgets, validated sources and schedules, and one bounded correction attempt.
11. Optional origin, ordered intermediate stops, departure/return dates, transport mode, hotel preferences and selected-offer IDs for planning.
12. Validated transport search contracts for car/train/flight/bus and hotel search contracts for dates, rooms and star filters; empty provider registries return explicit unavailable results.
13. Licensed-provider adapter interfaces, conservative readiness checks, safe booking-URL validation, nullable prices/links, and distinct live-quote versus estimate types.
14. Private expiring offer snapshots with TTL cleanup and selection checks for owner, expiry, route, dates, traveler count, currency and stay preferences.
15. Server-calculated totals, confirmed/estimated cost separation, budget status, incomplete-cost cautions and prevention of duplicate selected hotel charges.
16. Saved plan retrieval and formatted PDF downloads with route/dates, day cards, accommodation/transport choices, cost summaries, cautions and sources.
17. Public capability discovery backed by database, provider and model checks; production frontend CORS, cookie credentials and trusted-Origin write protection.
18. Simplified code with clear names, explicit conditions, focused planner/login/PDF helpers, fewer duplicated checks and test-only options, folder guides and a demo walkthrough.
19. Backward-compatible existing endpoints, an exact exported OpenAPI contract and frontend integration documentation; no frontend repository edits.

## Verification completed before this push

- All 196 automated tests passed.
- All 13 real-service smoke-check groups passed in one complete run, using actual MongoDB, local Ollama, web search and Fish Audio.
- Both workspace packages built successfully.
- The exported OpenAPI document exactly matches the current backend.
- Legacy and dated PDFs were parsed, rendered and visually inspected. The fresh MP3 was verified as a playable file of approximately 93 seconds.
- Live production checks passed for health, MongoDB, Google Continue redirect/session-cookie attributes, authenticated profile persistence, transport/hotel unavailable responses, validation, cookie Origin protection, PUT CORS and logout.
- Temporary test databases and the exact production test account/resources were removed.

Real-service groups: health and MongoDB connection; discovery, filters, pagination, numeric and readable detail routes; real account registration, duplicate protection, login and authentication; real profile onboarding and MongoDB persistence; honest unavailable transport and hotel searches; real story generation, text cleaning, persistence and cache reuse; real Fish Audio MP3 generation; real follow-ups, session persistence, three-pair DB retention and retry IDs; real itinerary, budget arithmetic, saved plan and PDF download; real dated itinerary with unavailable inventory and route/date PDF; live local capability probes; real resource ownership and logout/token revocation; production frontend CORS.

## Current production readiness and limits

```json
{
  "transport_search": false,
  "hotel_search": false,
  "extended_planning": false,
  "profile_onboarding": true
}
```

Profile onboarding is operational. Live transport/hotel inventory is not integrated: no licensed account or registered, authenticated vendor adapter is available. Search contracts are deployed and honestly return unavailable; fixture-based adapter tests are not live vendor verification. Production AI did not pass the bounded readiness probe and still needs a valid Gemini configuration or reachable hosted Ollama service. Local Ollama generation was tested successfully.

Gemini success paths are tested with simulated responses until a real key is supplied. Google callback logic is exercised with signed test identities and a live redirect check; final browser consent/account selection remains a user step. Destination autocomplete is not implemented; manual worldwide location entry remains supported. No test suite can guarantee every future upstream response or independently verify every generated historical statement.

See [the API guide](travel-api.md) and [the exported contract](openapi.json) for exact requests, responses, configuration requirements and compatibility details.
