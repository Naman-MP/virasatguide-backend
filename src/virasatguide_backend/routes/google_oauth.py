"""Google OpenID Connect sign-in and application token issuance."""

import os
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from httpx import HTTPError
from joserfc.errors import JoseError
from pymongo.errors import DuplicateKeyError
from starlette.middleware.sessions import SessionMiddleware

from virasatguide_backend.data.database import get_users_collection
from virasatguide_backend.data.security import (
    AUTH_COOKIE, ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token, get_secret_key, get_frontend_url,
)

router = APIRouter(prefix="/auth/google", tags=["Authentication"])


class GoogleOAuthSessionMiddleware:
    """Load the signing key after application configuration is available."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/auth/google/") or scope["path"] == "/auth/google/status":
            return await self.app(scope, receive, send)
        redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "")
        middleware = SessionMiddleware(
            self.app, secret_key=get_secret_key(), session_cookie="google_oauth",
            max_age=600, same_site="lax", https_only=redirect_uri.startswith("https://"),
            path="/auth/google",
        )
        await middleware(scope, receive, send)


def get_google_client():
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "")
    parsed = urlsplit(redirect_uri)
    if not client_id or not client_secret or not parsed.netloc or (
        parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
        )
    ):
        raise HTTPException(503, "Configure Google OAuth credentials and redirect URI")
    oauth = OAuth()
    return oauth.register(
        name="google", client_id=client_id, client_secret=client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256", "timeout": 10},
    )


@router.get("/status")
async def google_status():
    """Expose readiness only; never return credentials or contact Google."""
    try:
        get_google_client()
        get_secret_key()
    except (HTTPException, RuntimeError):
        return JSONResponse(
            {"configured": False, "message": "Google sign-in is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in the backend .env, and check the callback URL and JWT signing key."},
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse({"configured": True}, headers={"Cache-Control": "no-store"})


def oauth_failure(request: Request, status: int, code: str, message: str):
    request.session.clear()
    frontend_url = get_frontend_url()
    if frontend_url:
        parsed = urlsplit(frontend_url)
        query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "auth_error"]
        query.append(("auth_error", code))
        destination = urlunsplit(parsed._replace(query=urlencode(query)))
        return RedirectResponse(destination, status_code=303, headers={"Cache-Control": "no-store"})
    return JSONResponse({"detail": message}, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("/login")
async def google_login(request: Request, google=Depends(get_google_client)):
    try:
        return await google.authorize_redirect(request, os.environ["GOOGLE_REDIRECT_URI"])
    except (HTTPError, OAuthError):
        return oauth_failure(request, 502, "unavailable", "Google sign-in is temporarily unavailable")


@router.get("/callback")
async def google_callback(
    request: Request, users=Depends(get_users_collection), google=Depends(get_google_client),
):
    try:
        # Authlib verifies state, signature, issuer, audience, expiration and nonce.
        token = await google.authorize_access_token(request)
    except (OAuthError, JoseError, ValueError):
        code = "cancelled" if request.query_params.get("error") == "access_denied" else "invalid"
        return oauth_failure(request, 400, code, "Google sign-in failed; please start again")
    except HTTPError:
        return oauth_failure(request, 502, "unavailable", "Google sign-in is temporarily unavailable")
    request.session.clear()
    info = token.get("userinfo") or {}
    subject = info.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 255 or info.get("email_verified") is not True:
        return oauth_failure(request, 401, "unverified", "A verified Google account is required")
    # Stable provider identity; never link a password account by email alone.
    username = "google:" + subject
    user = await users.find_one({"username": username})
    if user is None:
        user = {
            "username": username, "google_sub": subject, "email": info.get("email"),
            "name": info.get("name"), "token_version": 1,
        }
        try:
            await users.insert_one(user.copy())
        except DuplicateKeyError:
            user = await users.find_one({"username": username})
    if user is None or user.get("google_sub") != subject:
        return oauth_failure(request, 409, "conflict", "Google account conflicts with an existing username")
    access_token = create_access_token({"sub": user["username"], "version": user.get("token_version", 1)})
    frontend_url = get_frontend_url()
    if frontend_url:
        response = RedirectResponse(frontend_url, status_code=303, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            AUTH_COOKIE, access_token, httponly=True, secure=os.environ["GOOGLE_REDIRECT_URI"].startswith("https://"),
            samesite="lax", max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60, path="/",
        )
        return response
    return JSONResponse(
        {"access_token": access_token,
         "token_type": "bearer"},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
