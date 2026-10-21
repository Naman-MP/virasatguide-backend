"""Shared MongoDB connection lifecycle and request dependencies."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    uri = os.getenv("MONGODB_URI")
    if not uri or "<db_password>" in uri:
        raise RuntimeError("Set MONGODB_URI in .env or the environment before starting.")

    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000, timeoutMS=10000)
    try:
        await client.admin.command("ping")
        app.state.database = client[os.getenv("MONGODB_DB", "virasatguide")]
        yield
    finally:
        await client.close()
        app.state.database = None


def get_database(request: Request) -> AsyncDatabase:
    """Reuse the application's connection pool for each request."""
    return request.app.state.database


async def is_database_available(database: AsyncDatabase) -> bool:
    try:
        await database.command("ping")
    except PyMongoError:
        return False
    return True
