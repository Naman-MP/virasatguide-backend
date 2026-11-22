# Travel endpoints

`router.py` exposes tour sessions, questions, plans, PDFs, provider searches and capability discovery. `schemas.py` retains the original plan contract and adds optional journey fields. `search_models.py` defines the search/quote contracts.

`search_providers.py` contains the licensed-provider interfaces and empty registries. `search.py` validates results and saves private, expiring offer snapshots. No real inventory vendor is integrated yet. `planning_offers.py` resolves selected offers and separates confirmed quotes from model estimates. `capabilities.py` probes operational readiness rather than trusting route/key presence.

`planner.py` researches the route, checks the draft and calculates estimates. `retrieval.py` answers questions from saved tour evidence. `pdf.py` renders saved plans without new AI/provider calls. `llm.py` uses the shared Gemini/Ollama client.

See the root `docs/travel-api.md` and `docs/openapi.json` for exact frontend integration details.


`places.py` provides public worldwide city suggestions through Open-Meteo/GeoNames with bounded requests and validated labels/coordinates. It is not a travel inventory or route-distance provider. Manual entry remains available on failures.

## Request and data flow

| Workflow | Path through code |
| --- | --- |
| Readiness | [capabilities.py](capabilities.py) checks database access, registered inventory providers and a short model probe. |
| Autocomplete | [places.py](places.py) validates the query and returns up to six usable Open-Meteo locations. Short input returns an empty list. |
| Create or resume a private tour | [router.py](router.py) takes a story snapshot, saves owner and evidence, and returns up to three recent exchanges. |
| Ask a tour question | [retrieval.py](retrieval.py) ranks saved passages. Model answers must cite supplied evidence. The router resolves matching request IDs and uses a version check to avoid concurrent overwrites. |
| Transport and hotel search | [search.py](search.py) calls ready and compatible [adapters](search_providers.py), validates quotes and stores owner-bound snapshots with expiry. Missing inventory returns an unavailable status. |
| Generate an itinerary | [planner.py](planner.py) researches the trip, validates model stops and schedules, and calculates totals. [planning_offers.py](planning_offers.py) checks selected offers and separates quotes from estimates. |
| Structured input and output | [llm.py](llm.py) delegates model generation. [schemas.py](schemas.py) and [search_models.py](search_models.py) define the data contracts and validation. |
| Reopen or download | [router.py](router.py) reads the owner's saved plan. [pdf.py](pdf.py) renders that same record for PDF export. |

Reuse a tour question's request ID after a retryable conflict. The same saved question returns its existing answer while retained. A different question submitted with a retained ID returns 409. See the [travel API guide](../../../docs/travel-api.md) for exact payloads and error cases.
