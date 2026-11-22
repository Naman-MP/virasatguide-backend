"""Audit login-name collisions, optionally backfill fields and add the unique index.

Run with the backend stopped. Dry-run is the default; --apply enables writes.
Never prints account names, passwords, hashes or the connection URI.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError
from virasatguide_backend.data.credentials import migrate_login_names


async def main():
    """Parse the migration options and audit or update the selected database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Explicit MongoDB database to audit")
    parser.add_argument("--apply", action="store_true", help="Apply the audited, idempotent migration")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise SystemExit("Set MONGODB_URI privately before running this migration")
    try:
        async with AsyncMongoClient(uri, serverSelectionTimeoutMS=5000, timeoutMS=10000) as client:
            result = await migrate_login_names(client[args.database].users, apply=args.apply)
        print(json.dumps(result))
    except PyMongoError:
        raise SystemExit("Migration could not complete. Check database access and index conflicts privately, then rerun. No accounts were deleted.") from None


if __name__ == "__main__":
    asyncio.run(main())
