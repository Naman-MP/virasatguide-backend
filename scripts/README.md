# Manual verification script

Run `uv run python scripts/smoke_backend.py` from the project root to check the full backend against configured real services.

The script uses a temporary MongoDB database, public monument data, disposable accounts and real model/audio calls. It checks discovery, stories, audio, questions, three-pair history retention, plans, PDFs and access control, then deletes its temporary database. Real user accounts and chats are not copied or deleted.

Use `uv run pytest -q` for normal offline checks. The real-service script needs working Atlas, an AI provider and a Fish Audio key.


`migrate_login_names.py --database NAME` audits legacy login names without writing. `--apply` backfills login metadata and creates the partial unique index; it never changes stable account keys, hashes or other collections. Pause writers and resolve collisions before applying. Detailed steps are in `docs/frontend-contract.md`.
