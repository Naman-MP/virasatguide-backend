# Database and login helpers

- `database.py` opens one MongoDB client at startup and closes it on shutdown.
- `monuments.py` searches, filters and loads monument records.
- `security.py` hashes passwords, signs login tokens and validates frontend settings.
- `stories.py` creates a cache key from monument details and model settings.

Passwords are stored as hashes. API keys stay in environment settings. Existing story cache keys remain usable while Gemini is not configured.

`profiles.py` validates submitted traveler data and projects public account fields. `travel_offers` has a MongoDB TTL index; selected offers also check ownership and expiry in the query.


`credentials.py` validates chosen login names/passwords, derives public credential status, reserves legacy names, and audits/backfills normalized login fields with a unique partial index. Stable `username` and `_id` identities stay unchanged. `profiles.py` accepts an optional birth date, independently of credential setup.

## Storage flow

| File | Responsibility |
| --- | --- |
| [database.py](database.py) | Startup, shutdown, shared connection, ping, indexes and login migration |
| [credentials.py](credentials.py) | Credential validation, login-name matching, private legacy audit |
| [security.py](security.py) | Password hashes, JWTs and trusted origins |
| [profiles.py](profiles.py) | Editable field rules and public account projection |
| [monuments.py](monuments.py) | Catalogue filters, sort order, projections and slugs |
| [stories.py](stories.py) | Cache identity from monument content and active models |

The stable account key controls ownership across both Google and password login. The normalized login index covers accounts with chosen names. Legacy collisions reserve the name and preserve original casing. The stable Google subject index path uses the unique internal username to protect concurrent signup. The monument ID and story cache key also have unique indexes. Travel offers include owner and expiry fields and a TTL index. Private sessions and saved plans are retrieved by owner. See the [migration guide](../../../docs/frontend-contract.md#login-name-migration).
