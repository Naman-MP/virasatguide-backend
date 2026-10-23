"""Virasat Guide application setup."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .data.security import get_frontend_origin
from dotenv import load_dotenv
from pathlib import Path

from .data.database import lifespan
from .routes.health import router as health_router
from .routes.auth import router as auth_router

from .routes.google_oauth import router as google_router, GoogleOAuthSessionMiddleware

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

app = FastAPI(title="Virasat Guide API", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(health_router)

app.add_middleware(GoogleOAuthSessionMiddleware)
app.include_router(google_router)

frontend_origin = get_frontend_origin()
if frontend_origin:
    app.add_middleware(
        CORSMiddleware, allow_origins=[frontend_origin], allow_credentials=True,
        allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Authorization"],
    )
