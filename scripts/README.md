# Manual verification script

Run `uv run python scripts/smoke_backend.py` from the project root to check the full backend against configured real services.

The script uses a temporary MongoDB database, public monument data, disposable accounts and real model/audio calls. It checks discovery, stories, audio, questions, three-pair history retention, plans, PDFs and access control, then deletes its temporary database. Real user accounts and chats are not copied or deleted.

Use `uv run pytest -q` for normal offline checks. The real-service script needs working Atlas, an AI provider and a Fish Audio key.


`migrate_login_names.py --database NAME` audits legacy login names without writing. `--apply` backfills login metadata and creates the partial unique index; it never changes stable account keys, hashes or other collections. Pause writers and resolve collisions before applying. Detailed steps are in `docs/frontend-contract.md`.

| Script | Data and prerequisites |
| --- | --- |
| [migrate_login_names.py](migrate_login_names.py) | Audits the configured database. Apply mode backfills eligible login records and ensures the partial unique index exists. |
| [smoke_backend.py](smoke_backend.py) | Copies public monument records into a disposable Atlas database, creates QA users, calls configured generation and audio services, verifies saved records and files, and drops the temporary database. |

Use the [automated test guide](../tests/README.md) for tests with simulated providers. The [demo QA handoff](../docs/frontend-demo-qa.md) distinguishes these from live service checks.
