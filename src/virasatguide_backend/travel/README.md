# Travel endpoints

`router.py` exposes tour sessions, questions, plans, PDFs, provider searches and capability discovery. `schemas.py` retains the original plan contract and adds optional journey fields. `search_models.py` defines the search/quote contracts.

`search_providers.py` contains the licensed-provider interfaces and empty registries. `search.py` validates results and saves private, expiring offer snapshots. No real inventory vendor is integrated yet. `planning_offers.py` resolves selected offers and separates confirmed quotes from model estimates. `capabilities.py` probes operational readiness rather than trusting route/key presence.

`planner.py` researches the route, checks the draft and calculates estimates. `retrieval.py` answers questions from saved tour evidence. `pdf.py` renders saved plans without new AI/provider calls. `llm.py` uses the shared Gemini/Ollama client.

See the root `docs/travel-api.md` and `docs/openapi.json` for exact frontend integration details.


`places.py` provides public worldwide city suggestions through Open-Meteo/GeoNames with bounded requests and validated labels/coordinates. It is not a travel inventory or route-distance provider. Manual entry remains available on failures.
