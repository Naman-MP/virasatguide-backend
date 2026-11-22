"""Shared MongoDB connection lifecycle and request dependencies."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from virasatguide_backend.data.security import get_secret_key
from virasatguide_backend.data.credentials import migrate_login_names


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open MongoDB, check startup configuration, prepare indexes, and release the connection on shutdown."""
    uri = os.getenv("MONGODB_URI")
    if not uri or "<db_password>" in uri:
        raise RuntimeError("Set MONGODB_URI in .env or the environment before starting.")

    # Check configuration before starting the app.
    get_secret_key()

    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000, timeoutMS=10000)
    try:
        await client.admin.command("ping")
        app.state.database = client[os.getenv("MONGODB_DB", "virasatguide")]
        # Keep account and public monument IDs unique and fast to look up.
        await app.state.database.users.create_index("username", unique=True)
        await migrate_login_names(app.state.database.users, apply=True)
        await app.state.database.monuments.create_index("id", unique=True)
        await app.state.database.tour_stories.create_index("story_key", unique=True)
        await app.state.database.travel_offers.create_index("expires_at", expireAfterSeconds=0)
        yield
    finally:
        await client.close()
        app.state.database = None


def get_database(request: Request) -> AsyncDatabase:
    """Reuse the application's connection pool for each request."""
    return request.app.state.database


async def is_database_available(database: AsyncDatabase) -> bool:
    """Check whether MongoDB responds to a ping."""
    try:
        await database.command("ping")
    except PyMongoError:
        return False
    return True


def get_users_collection(request: Request):
    # Reuse the shared connection for authentication.
    """Return the request's users collection from the shared database connection."""
    return get_database(request).users
