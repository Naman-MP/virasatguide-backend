import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    uri = os.getenv("MONGODB_URI")
    if not uri or "<db_password>" in uri:
        raise RuntimeError("Set MONGODB_URI in .env or the environment before starting.")
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000, timeoutMS=10000)
    try:
        await client.admin.command("ping")
        app.state.mongodb_client = client
        app.state.database = client[os.getenv("MONGODB_DB", "virasatguide")]
        yield
    finally:
        await client.close()


app = FastAPI(title="Virasat Guide API", lifespan=lifespan)


@app.get("/")
async def root():
    return {"message": "Virasat Guide API is running. Try GET /health"}


@app.get("/health")
async def health(request: Request):
    try:
        await request.app.state.mongodb_client.admin.command("ping")
        return {"status": "ok", "database": "connected"}
    except PyMongoError:
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "disconnected"})


@app.get("/ping-db")
async def ping_db(request: Request):
    try:
        await request.app.state.mongodb_client.admin.command("ping")
        return {"ok": True, "message": "MongoDB is reachable"}
    except PyMongoError:
        return JSONResponse(status_code=503, content={"ok": False, "error": "MongoDB is unavailable"})
