"""Virasat Guide application setup."""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .data.security import get_frontend_origin

from .data.database import lifespan
from .routes.health import router as health_router
from .routes.monuments import router as monuments_router
from .routes.agent_stories import router as agent_stories_router
from .routes.tts import router as tts_router
from .travel.router import router as travel_router
from .routes.auth import router as auth_router

from .routes.google_oauth import router as google_router, GoogleOAuthSessionMiddleware

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

app = FastAPI(title="Virasat Guide API", lifespan=lifespan)
for router in (auth_router, health_router, monuments_router, agent_stories_router,
               tts_router, travel_router, google_router):
    app.include_router(router)

app.add_middleware(GoogleOAuthSessionMiddleware)

allowed_origins = ["https://virasat-frontend-nu.vercel.app"]
frontend_origin = get_frontend_origin()
if frontend_origin and frontend_origin not in allowed_origins:
    allowed_origins.append(frontend_origin)

app.add_middleware(
    CORSMiddleware, allow_origins=allowed_origins, allow_credentials=True,
    allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type", "Authorization"],
)
