"""Google OpenID Connect sign-in and application token issuance."""

import os
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from httpx import HTTPError
from joserfc.errors import JoseError
from pymongo.errors import DuplicateKeyError
from starlette.middleware.sessions import SessionMiddleware

from virasatguide_backend.data.database import get_users_collection
from virasatguide_backend.data.security import (
    AUTH_COOKIE, ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token, get_secret_key, get_frontend_url, get_frontend_origin,
)

router = APIRouter(prefix="/auth/google", tags=["Authentication"])


class GoogleOAuthSessionMiddleware:
    """Load the signing key after application configuration is available."""

    def __init__(self, app):
        """Keep the wrapped app so OAuth session settings can be loaded for each request."""
        self.app = app

    async def __call__(self, scope, receive, send):
        """Apply session middleware to Google OAuth routes that need transient state."""
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if not path.startswith("/auth/google/") or path == "/auth/google/status":
            return await self.app(scope, receive, send)
        redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "")
        middleware = SessionMiddleware(
            self.app, secret_key=get_secret_key(), session_cookie="google_oauth",
            max_age=600, same_site="lax", https_only=redirect_uri.startswith("https://"),
            path="/auth/google",
        )
        await middleware(scope, receive, send)


def get_google_redirect_uri() -> str:
    """Validate the configured callback URL and require public HTTPS in production."""
    value = os.getenv("GOOGLE_REDIRECT_URI", "").strip()
    parsed = urlsplit(value)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    valid_scheme = parsed.scheme == "https" or (parsed.scheme == "http" and local)
    has_extra_parts = parsed.username or parsed.password or parsed.query or parsed.fragment
    if not parsed.netloc or has_extra_parts or parsed.path != "/auth/google/callback" or not valid_scheme:
        raise HTTPException(503, "Set GOOGLE_REDIRECT_URI to the backend's /auth/google/callback URL")
    if os.getenv("VERCEL_ENV") == "production" and (local or parsed.scheme != "https"):
        raise HTTPException(503, "Production GOOGLE_REDIRECT_URI must use the live HTTPS backend, not localhost")
    return value


def get_google_client():
    """Configure Authlib to use Google's OpenID metadata and PKCE."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    get_google_redirect_uri()
    if not client_id or not client_secret:
        raise HTTPException(503, "Configure Google OAuth credentials and redirect URI")
    return OAuth().register(
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
    """Clear transient OAuth state and return a safe error to the client or frontend."""
    request.session.clear()
    frontend_url = get_frontend_url()
    if frontend_url:
        parsed = urlsplit(frontend_url)
        query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "auth_error"]
        query.append(("auth_error", code))
        destination = urlunsplit(parsed._replace(query=urlencode(query)))
        return RedirectResponse(destination, status_code=303, headers={"Cache-Control": "no-store"})
    return JSONResponse({"detail": message}, status_code=status, headers={"Cache-Control": "no-store"})


async def start_google_flow(request: Request, google, purpose: str):
    """Remember which Google flow started and redirect the browser to authorization."""
    request.session["purpose"] = purpose
    try:
        return await google.authorize_redirect(request, get_google_redirect_uri())
    except (HTTPError, OAuthError):
        return oauth_failure(request, 502, "unavailable", "Google sign-in is temporarily unavailable")


@router.get("/login")
async def google_login(request: Request, google=Depends(get_google_client)):
    """Start the Google flow for accounts that already exist."""
    return await start_google_flow(request, google, "login")


@router.get("/continue")
async def google_continue(request: Request, google=Depends(get_google_client)):
    """Sign in an existing Google account or create it after Google verification."""
    return await start_google_flow(request, google, "continue")


@router.get("/signup")
async def google_signup(request: Request, google=Depends(get_google_client)):
    """Start the Google flow that allows creation of a new account."""
    return await start_google_flow(request, google, "signup")


def signup_required():
    """Return a signup prompt to a new user who entered through the legacy login route."""
    return HTMLResponse(
        "<html><body><h1>No VirasatGuide account found</h1>"
        "<p>Sign up with this Google account to finish creating your account.</p>"
        '<p><a href="/auth/google/signup">Continue with Google to sign up</a></p>'
        "</body></html>",
        status_code=403,
        headers={"Cache-Control": "no-store"},
    )


def create_login_response(access_token: str):
    """Return a login token, or set its cookie and send the browser to the frontend."""
    frontend_url = get_frontend_url()
    if frontend_url:
        response = RedirectResponse(frontend_url, status_code=303, headers={"Cache-Control": "no-store"})
        backend = urlsplit(get_google_redirect_uri())
        secure = backend.scheme == "https"
        cross_origin = get_frontend_origin() != f"{backend.scheme}://{backend.netloc}"
        response.set_cookie(
            AUTH_COOKIE, access_token, httponly=True, secure=secure,
            samesite="none" if secure and cross_origin else "lax",
            max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60, path="/",
        )
        return response
    return JSONResponse(
        {"access_token": access_token,
         "token_type": "bearer"},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get("/callback")
async def google_callback(
    request: Request, users=Depends(get_users_collection), google=Depends(get_google_client),
):
    """Verify Google identity, resolve the stable account, and start an application session."""
    try:
        # Authlib verifies state, signature, issuer, audience, expiration and nonce.
        token = await google.authorize_access_token(request)
    except (OAuthError, JoseError, ValueError):
        code = "cancelled" if request.query_params.get("error") == "access_denied" else "invalid"
        return oauth_failure(request, 400, code, "Google sign-in failed; please start again")
    except HTTPError:
        return oauth_failure(request, 502, "unavailable", "Google sign-in is temporarily unavailable")
    purpose = request.session.get("purpose")
    request.session.clear()
    info = token.get("userinfo") or {}
    subject = info.get("sub")
    email = info.get("email")
    if (not isinstance(subject, str) or not subject or len(subject) > 255 or subject.strip() != subject
        or not isinstance(email, str) or len(email) > 320 or not re.fullmatch(r"[^\s@]+@[^\s@]+", email)
        or info.get("email_verified") is not True):
        return oauth_failure(request, 401, "unverified", "A verified Google account is required")
    # Stable provider identity; never link a password account by email alone.
    username = "google:" + subject
    user = await users.find_one({"username": username})
    if user is None:
        if purpose not in {"signup", "continue"}:
            return signup_required()
        user = {
            "username": username, "google_sub": subject, "email": info.get("email"),
            "name": info.get("name"), "full_name": info.get("name"),
            "onboarding_completed": False, "token_version": 1, "credentials_set": False,
        }
        try:
            await users.insert_one(user.copy())
        except DuplicateKeyError:
            user = await users.find_one({"username": username})
    if user is None or user.get("google_sub") != subject:
        return oauth_failure(request, 409, "conflict", "Google account conflicts with an existing username")
    access_token = create_access_token({"sub": user["username"], "version": user.get("token_version", 1)})
    return create_login_response(access_token)
