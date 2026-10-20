from contextlib import asynccontextmanager
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    uri = os.getenv("MONGODB_URI", "")
    if not uri or "<db_password>" in uri:
        raise RuntimeError("Set MONGODB_URI in your private .env file.")
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=10000)
    try:
        database = client[os.getenv("MONGODB_DB", "virasatguide")]
        try:
            await database.command("ping")
        except PyMongoError:
            raise RuntimeError(
                "Atlas connection failed. Check your URI, database password, and IP access list."
            ) from None
        app.state.database = database
        yield
    finally:
        await client.close()


app = FastAPI(title="Virasat Guide API", version="1.0.0", lifespan=lifespan)


@app.get("/health", tags=["Health"])
async def health(request: Request):
    try:
        await request.app.state.database.command("ping")
    except PyMongoError:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "database": "disconnected"},
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        content={"status": "ok", "database": "connected"},
        headers={"Cache-Control": "no-store"},
    )
