"""Virasat Guide application setup."""

from fastapi import FastAPI

from virasatguide_backend.data.database import lifespan
from virasatguide_backend.routes.health import router as health_router

app = FastAPI(title="Virasat Guide API", lifespan=lifespan)
app.include_router(health_router)
