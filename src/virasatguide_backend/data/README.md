# Database and login helpers

- `database.py` opens one MongoDB client at startup and closes it on shutdown.
- `monuments.py` searches, filters and loads monument records.
- `security.py` hashes passwords, signs login tokens and validates frontend settings.
- `stories.py` creates a cache key from monument details and model settings.

Passwords are stored as hashes. API keys stay in environment settings. Existing story cache keys remain usable while Gemini is not configured.

`profiles.py` validates submitted traveler data and projects public account fields. `travel_offers` has a MongoDB TTL index; selected offers also check ownership and expiry in the query.


`credentials.py` validates chosen login names/passwords, derives public credential status, reserves legacy names, and audits/backfills normalized login fields with a unique partial index. Stable `username` and `_id` identities stay unchanged. `profiles.py` accepts an optional birth date, independently of credential setup.
