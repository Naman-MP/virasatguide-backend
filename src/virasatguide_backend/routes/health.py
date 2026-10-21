"""Application status endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pymongo.asynchronous.database import AsyncDatabase

from virasatguide_backend.data.database import get_database, is_database_available

router = APIRouter(tags=["Health"])
Database = Annotated[AsyncDatabase, Depends(get_database)]


@router.get("/")
async def root():
    return {"message": "Virasat Guide API is running. Try GET /health"}


@router.get("/health")
async def health(database: Database):
    available = await is_database_available(database)
    return JSONResponse(
        status_code=200 if available else 503,
        content={
            "status": "ok" if available else "unavailable",
            "database": "connected" if available else "disconnected",
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/ping-db")
async def ping_db(database: Database):
    if await is_database_available(database):
        return {"ok": True, "message": "MongoDB is reachable"}
    return JSONResponse(
        status_code=503,
        content={"ok": False, "error": "MongoDB is unavailable"},
    )
